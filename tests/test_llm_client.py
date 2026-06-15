from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from boundary_analyzer.llm.client import call_llm


class LllmClientTest(unittest.TestCase):
    def test_no_api_key_returns_none(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            result = call_llm("test prompt")
            self.assertIsNone(result)

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_successful_call_returns_content(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "Hello world"}}]}
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = call_llm("test prompt")
            self.assertEqual(result, "Hello world")

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_429_retries_then_fallback(self, mock_post):
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.json.return_value = {"error": {"metadata": {"retry_after_seconds": 1}}}

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"choices": [{"message": {"content": "fallback ok"}}]}

        mock_post.side_effect = [mock_429, mock_429, mock_429, mock_200]

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = call_llm("test prompt")
            self.assertEqual(result, "fallback ok")

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_timeout_retries_then_fallback(self, mock_post):
        from requests import Timeout

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"choices": [{"message": {"content": "fallback ok"}}]}

        mock_post.side_effect = [Timeout(), Timeout(), Timeout(), mock_200]

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = call_llm("test prompt")
            self.assertEqual(result, "fallback ok")

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_empty_content_skips_to_fallback(self, mock_post):
        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = {"choices": [{"message": {"content": None}}]}

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {"choices": [{"message": {"content": "fallback ok"}}]}

        mock_post.side_effect = [mock_empty, mock_ok]

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = call_llm("test prompt")
            self.assertEqual(result, "fallback ok")

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_all_models_fail_returns_none(self, mock_post):
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.json.return_value = {"error": {"metadata": {"retry_after_seconds": 1}}}

        mock_post.return_value = mock_429

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from boundary_analyzer.llm.client import call_llm

            result = call_llm("test prompt")
            self.assertIsNone(result)

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_custom_model_override(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "custom model"}}]}
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = call_llm("test prompt", model="my-custom-model")
            self.assertEqual(result, "custom model")
            call_kwargs = mock_post.call_args[1]
            self.assertEqual(call_kwargs["json"]["model"], "my-custom-model")

    @patch("boundary_analyzer.llm.client.requests.post")
    def test_temperature_and_max_tokens_passed(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            call_llm("test", temperature=0.5, max_tokens=999)
            call_kwargs = mock_post.call_args[1]
            self.assertEqual(call_kwargs["json"]["temperature"], 0.5)
            self.assertEqual(call_kwargs["json"]["max_tokens"], 999)


if __name__ == "__main__":
    unittest.main()
