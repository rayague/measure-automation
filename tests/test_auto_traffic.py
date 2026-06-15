import unittest
from unittest.mock import MagicMock, patch

from boundary_analyzer.auto.models import Endpoint
from boundary_analyzer.auto.traffic import (
    _LLM_MODELS,
    _LLM_PROMPTS,
    TrafficConfig,
    _generate_graphql_payload,
    _generate_llm_payload,
    _is_graphql_path,
    _is_llm_path,
)


class GraphQLTest(unittest.TestCase):
    def test_is_graphql_path(self):
        self.assertTrue(_is_graphql_path("/graphql"))
        self.assertTrue(_is_graphql_path("/api/graphql"))
        self.assertTrue(_is_graphql_path("/query"))
        self.assertFalse(_is_graphql_path("/api/users"))
        self.assertFalse(_is_graphql_path("/health"))

    def test_generate_graphql_payload_no_args(self):
        payload = _generate_graphql_payload("users", [])
        self.assertIn("query", payload)
        self.assertIn("users", payload["query"])

    def test_generate_graphql_payload_with_args(self):
        args = [{"name": "id", "type": "String"}]
        payload = _generate_graphql_payload("user", args)
        self.assertIn("query", payload)
        self.assertIn("user", payload["query"])
        self.assertIn("id:", payload["query"])

    @patch("boundary_analyzer.auto.traffic.requests.post")
    def test_discover_endpoints_graphql(self, mock_post):
        from boundary_analyzer.auto.traffic import discover_endpoints_graphql

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "__schema": {
                    "queryType": {"name": "Query"},
                    "mutationType": {"name": "Mutation"},
                    "types": [
                        {
                            "kind": "OBJECT",
                            "name": "Query",
                            "fields": [
                                {
                                    "name": "users",
                                    "args": [{"name": "limit", "type": {"name": "Int"}}],
                                },
                                {"name": "health", "args": []},
                            ],
                        },
                        {
                            "kind": "OBJECT",
                            "name": "Mutation",
                            "fields": [
                                {
                                    "name": "createUser",
                                    "args": [{"name": "name", "type": {"name": "String"}}],
                                }
                            ],
                        },
                    ],
                }
            }
        }
        mock_post.return_value = mock_resp

        config = TrafficConfig()
        eps = discover_endpoints_graphql("127.0.0.1", 8080, config)
        self.assertEqual(len(eps), 3)
        self.assertTrue(all(ep.is_graphql for ep in eps))
        self.assertTrue(any(ep.graphql_field == "users" for ep in eps))
        self.assertTrue(any(ep.graphql_field == "health" for ep in eps))
        self.assertTrue(any(ep.graphql_field == "createUser" for ep in eps))


class LLMTest(unittest.TestCase):
    def test_is_llm_path(self):
        self.assertTrue(_is_llm_path("/v1/chat/completions"))
        self.assertTrue(_is_llm_path("/chat"))
        self.assertTrue(_is_llm_path("/api/chat"))
        self.assertTrue(_is_llm_path("/generate"))
        self.assertFalse(_is_llm_path("/api/users"))

    def test_generate_llm_payload_format(self):
        payload = _generate_llm_payload()
        self.assertIn("model", payload)
        self.assertIn("messages", payload)
        self.assertIn("temperature", payload)
        self.assertIn("max_tokens", payload)
        self.assertIn(payload["model"], _LLM_MODELS)
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertIn(payload["messages"][1]["content"], _LLM_PROMPTS)


class OAuth2Test(unittest.TestCase):
    def setUp(self):
        self.config = TrafficConfig()

    @patch("boundary_analyzer.auto.traffic.requests.post")
    def test_try_oauth2_client_credentials(self, mock_post):
        from boundary_analyzer.auto.traffic import _try_oauth2_client_credentials

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "cc-token-123"}
        mock_post.return_value = mock_resp

        token = _try_oauth2_client_credentials("http://localhost:8080", self.config)
        self.assertEqual(token, "cc-token-123")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["grant_type"], "client_credentials")

    @patch("boundary_analyzer.auto.traffic.requests.post")
    def test_try_oauth2_password_grant(self, mock_post):
        from boundary_analyzer.auto.traffic import _try_oauth2_password_grant

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "pw-token-456"}
        mock_post.return_value = mock_resp

        token = _try_oauth2_password_grant("http://localhost:8080", self.config)
        self.assertEqual(token, "pw-token-456")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["grant_type"], "password")

    @patch("boundary_analyzer.auto.traffic.requests.post")
    def test_try_oauth2_refresh(self, mock_post):
        from boundary_analyzer.auto.traffic import _try_oauth2_refresh

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "refreshed-token"}
        mock_post.return_value = mock_resp

        token = _try_oauth2_refresh("http://localhost:8080", "old-token", self.config)
        self.assertEqual(token, "refreshed-token")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(kwargs["data"]["refresh_token"], "old-token")


class TrafficConfigTest(unittest.TestCase):
    def test_default_values(self):
        config = TrafficConfig()
        self.assertEqual(config.duration, 60)
        self.assertEqual(config.workers, 5)
        self.assertIsNone(config.auth_token)
        self.assertEqual(config.base_url, "http://127.0.0.1")


class EndpointGraphQLFieldsTest(unittest.TestCase):
    def test_endpoint_graphql_fields_default(self):
        ep = Endpoint(method="POST", path="/graphql")
        self.assertFalse(ep.is_graphql)
        self.assertEqual(ep.graphql_field, "")
        self.assertEqual(ep.graphql_args, [])

    def test_endpoint_graphql_fields_set(self):
        ep = Endpoint(
            method="POST",
            path="/graphql",
            is_graphql=True,
            graphql_field="users",
            graphql_args=[{"name": "limit", "type": "Int"}],
        )
        self.assertTrue(ep.is_graphql)
        self.assertEqual(ep.graphql_field, "users")
        self.assertEqual(len(ep.graphql_args), 1)


class LLMEndpointDetectionTest(unittest.TestCase):
    def test_llm_paths_in_traffic_config(self):
        from boundary_analyzer.auto.traffic import _LLM_PATHS

        self.assertIn("/v1/chat/completions", _LLM_PATHS)
        self.assertIn("/chat", _LLM_PATHS)
        self.assertIn("/generate", _LLM_PATHS)


class GraphQLPathDetectionTest(unittest.TestCase):
    def test_graphql_paths_in_traffic_config(self):
        from boundary_analyzer.auto.traffic import _GRAPHQL_PATHS

        self.assertIn("/graphql", _GRAPHQL_PATHS)
        self.assertIn("/query", _GRAPHQL_PATHS)
