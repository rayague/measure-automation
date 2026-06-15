from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from boundary_analyzer.llm.client import call_llm
from boundary_analyzer.llm.prompts import build_analysis_prompt

logger = logging.getLogger(__name__)


def _find_project_context(data_dir: Path) -> str:
    """Try to build some context about the analysed project from available data."""
    parts: list[str] = []

    mapping_path = data_dir / "interim" / "endpoint_table_map.csv"
    if mapping_path.exists():
        try:
            df = pd.read_csv(mapping_path)
            services = df["service_name"].unique().tolist() if "service_name" in df.columns else []
            if services:
                parts.append(f"Services found in traces: {', '.join(services)}")
        except (OSError, PermissionError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logger.warning("Could not read endpoint mapping: %s", e)

    endpoints_path = data_dir / "interim" / "endpoints.csv"
    if endpoints_path.exists():
        try:
            df = pd.read_csv(endpoints_path)
            if "service_name" in df.columns and "endpoint_key" in df.columns:
                for svc in df["service_name"].unique():
                    eps = df[df["service_name"] == svc]["endpoint_key"].unique().tolist()
                    parts.append(f"  {svc} endpoints: {', '.join(eps)}")
        except (OSError, PermissionError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logger.warning("Could not read endpoints CSV: %s", e)

    return "\n".join(parts) if parts else "Project context not available."


def _generate_local_analysis(
    rank_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
    spans_count: int,
    traces_count: int,
    scom_method: str,
) -> str:
    """Compute a local fallback analysis when the LLM is unavailable."""
    import itertools
    import statistics

    svc_col = "service_name" if "service_name" in rank_df.columns else rank_df.columns[0]
    score_col = "scom_score" if "scom_score" in rank_df.columns else "score"
    susp_col = "is_suspicious" if "is_suspicious" in rank_df.columns else None
    rank_col = "rank" if "rank" in rank_df.columns else None

    lines: list[str] = []
    lines.append("> **Analysis mode:** Local computed (LLM unavailable)")
    lines.append("> Set `OPENROUTER_API_KEY` environment variable to enable AI-powered analysis.\n")
    lines.append("## System Overview\n")

    # ── Aggregate statistics ──────────────────────────────────────────────
    scores = [float(v) for v in rank_df[score_col].dropna()] if score_col in rank_df.columns else []
    total_svc = len(rank_df)
    suspicious_count = int(rank_df[susp_col].sum()) if susp_col else 0
    healthy_count = total_svc - suspicious_count
    avg_scom = statistics.mean(scores) if scores else 0.0
    min_scom = min(scores) if scores else 0.0
    max_scom = max(scores) if scores else 0.0

    threshold = None
    if "threshold_value" in rank_df.columns:
        threshold = float(rank_df["threshold_value"].iloc[0])

    lines.append(
        f"This system contains **{total_svc} services** — **{suspicious_count} suspicious** (SCOM below "
        f"threshold) and **{healthy_count} healthy**."
    )
    lines.append(f"Average SCOM: **{avg_scom:.4f}** (range: {min_scom:.4f} – {max_scom:.4f}).")
    if threshold is not None:
        lines.append(f"Threshold: **{threshold}**. Services below this value may have boundary problems.")
    lines.append("")

    if total_svc > 0 and suspicious_count > 0:
        gap = (
            threshold - max(s for s in scores if s < threshold)
            if threshold and any(s < threshold for s in scores)
            else None
        )
        if gap is not None:
            lines.append(
                f"The closest suspicious service is **{gap:.4f}** below threshold "
                f"— indicating a clear separation between healthy and problematic services."
                if gap > 0.05
                else f"The closest suspicious service is only **{gap:.4f}** below threshold "
                f"— the boundary between healthy and suspicious is narrow."
            )
            lines.append("")

    # ── Per-service analysis ──────────────────────────────────────────────
    lines.append("## Service Cohesion Analysis\n")

    for svc in rank_df[svc_col].unique():
        svc_data = mapping_df[mapping_df["service_name"] == svc] if "service_name" in mapping_df.columns else mapping_df
        svc_rank = rank_df[rank_df[svc_col] == svc]
        if svc_rank.empty:
            continue

        score = float(svc_rank[score_col].iloc[0])
        is_susp = bool(svc_rank[susp_col].iloc[0]) if susp_col else False
        rank_val = int(svc_rank[rank_col].iloc[0]) if rank_col else 0

        status = "SUSPICIOUS" if is_susp else "healthy"
        lines.append(f"**{svc}** (SCOM: {score:.4f}, Rank: #{rank_val}, {status})\n")

        if "endpoint_key" in svc_data.columns and "table" in svc_data.columns:
            eps = svc_data.groupby("endpoint_key")
            ep_list = list(eps)

            # Data table
            lines.append("| Endpoint | Tables (call count) |")
            lines.append("|----------|---------------------|")
            for ep_name, ep_df in ep_list:
                if "count" in ep_df.columns:
                    table_info = ", ".join(f"{row['table']} ({int(row['count'])}x)" for _, row in ep_df.iterrows())
                else:
                    table_info = ", ".join(ep_df["table"].unique())
                lines.append(f"| `{ep_name}` | {table_info} |")
            lines.append("")

            # Jaccard computation
            if len(ep_list) >= 2:
                jaccard_pairs: list[tuple[str, str, set, set, float]] = []
                for (ep1_name, ep1_df), (ep2_name, ep2_df) in itertools.combinations(ep_list, 2):
                    tables1 = set(ep1_df["table"].unique())
                    tables2 = set(ep2_df["table"].unique())
                    shared = tables1 & tables2
                    union = tables1 | tables2
                    jaccard = len(shared) / len(union) if union else 0.0
                    jaccard_pairs.append((ep1_name, ep2_name, shared, union, jaccard))

                avg_jaccard = statistics.mean(j for _, _, _, _, j in jaccard_pairs)

                # Jaccard table
                lines.append("**Endpoint Pairwise Overlap (Jaccard Similarity):**")
                lines.append("| Endpoint A | Endpoint B | Shared Tables | Union Tables | Jaccard |")
                lines.append("|------------|------------|---------------|--------------|---------|")
                for ep1_name, ep2_name, shared, union, jaccard in jaccard_pairs:
                    shared_str = ", ".join(sorted(shared)) if shared else "(none)"
                    union_str = ", ".join(sorted(union))
                    lines.append(f"| `{ep1_name}` | `{ep2_name}` | {shared_str} | {union_str} | {jaccard:.2f} |")
                lines.append("")

                # Narrative
                if is_susp:
                    # Find low-overlap pairs
                    low_pairs = [(a, b, sh, un, j) for a, b, sh, un, j in jaccard_pairs if j < 0.7]
                    if low_pairs:
                        lines.append(
                            f"**Why — Root Cause:** The SCOM score of **{score:.4f}** flags this service because "
                            f"its endpoints access disjoint sets of tables (average Jaccard: **{avg_jaccard:.2f}**)."
                        )
                        for ep_a, ep_b, shared_t, union_t, jv in low_pairs:
                            diff = union_t - shared_t
                            if diff:
                                lines.append(
                                    f"- `{ep_a}` and `{ep_b}` share only **{len(shared_t)}/{len(union_t)}** "
                                    f"tables (J={jv:.2f}). Tables unique to one endpoint: "
                                    f"**{', '.join(sorted(diff))}** — "
                                    f"this suggests these endpoints belong to different bounded contexts."
                                )
                        lines.append("")
                        lines.append(
                            f"**Impact — Architectural Consequence:** The low overlap means a developer "
                            f"modifying one endpoint must understand tables accessed by other endpoints with "
                            f"different concerns. This creates change coupling — a modification to "
                            f"{', '.join(sorted(low_pairs[0][2])) if low_pairs[0][2] else 'shared data'} "
                            f"could break unrelated functionality. "
                            f"Deploying this service as a single unit also means all its endpoints scale together, "
                            f"even though they serve distinct data domains."
                        )
                        lines.append("")
                        lines.append(
                            f"**Quantified Suggestion — Refactor Plan:** Consider splitting `{svc}` "
                            f"into separate services based on table access patterns. "
                            f"Endpoints sharing **high-Jaccard** clusters should stay together."
                        )
                        for ep_a, ep_b, shared_t, union_t, jv in sorted(jaccard_pairs, key=lambda x: -x[4]):
                            if jv >= 0.7:
                                lines.append(
                                    f"- Keep `{ep_a}` + `{ep_b}` together "
                                    f"(J={jv:.2f}, shared: {', '.join(sorted(shared_t)) if shared_t else 'none'})"
                                )
                        for ep_a, ep_b, shared_t, union_t, jv in sorted(jaccard_pairs, key=lambda x: x[4]):
                            if jv < 0.7:
                                lines.append(
                                    f"- Split `{ep_a}` from `{ep_b}` "
                                    f"(J={jv:.2f}, disjoint: {', '.join(sorted(union_t - shared_t))})"
                                )
                        lines.append("")
                    else:
                        lines.append(
                            f"**Why:** Despite a low SCOM score of **{score:.4f}**, all endpoint pairs show "
                            f"high Jaccard overlap (≥0.7). The issue may be "
                            f"the total number of tables accessed relative to endpoints. "
                            f"Average Jaccard: **{avg_jaccard:.2f}**.\n"
                            if avg_jaccard >= 0.7
                            else ""
                        )
                else:
                    # Healthy service — positive narrative
                    if avg_jaccard >= 0.9:
                        lines.append(
                            f"This service has **very high cohesion** (average Jaccard: **{avg_jaccard:.2f}**). "
                            f"All endpoints share the same tables — the bounded context is well-defined. "
                            f"The service is correctly scoped.\n"
                        )
                    elif avg_jaccard >= 0.7:
                        lines.append(
                            f"This service has **good cohesion** (average Jaccard: **{avg_jaccard:.2f}**). "
                            f"Endpoints share most tables, indicating a coherent bounded context.\n"
                        )
                    else:
                        lines.append(
                            f"This service is above threshold but shows **moderate overlap** "
                            f"(average Jaccard: **{avg_jaccard:.2f}**). "
                            f"While not critical, there may be room to improve endpoint grouping.\n"
                        )
            else:
                lines.append("Single-endpoint service (no pairs to compare). SCOM reflects endpoint-to-table ratio.\n")
        else:
            lines.append("No endpoint-to-table mapping available for this service.\n")

    # ── Threshold Impact Analysis ──────────────────────────────────────────
    if threshold is not None and scores:
        lines.append("---\n")
        lines.append("### Threshold Impact Analysis\n")
        lines.append(
            f"Current threshold: **{threshold}**. This "
            f"{'captures ' + str(suspicious_count) + ' suspicious service(s)' if suspicious_count > 0 else 'flags no services as suspicious'}."
        )
        for delta in [0.05, 0.1]:
            candidate = threshold - delta
            count_below = sum(1 for s in scores if s < candidate)
            lines.append(
                f"- At threshold **{candidate:.4f}** (−{delta:.2f}): **{count_below}** "
                f"{'service would be suspicious' if count_below == 1 else 'services would be suspicious'}."
            )
            candidate_up = threshold + delta
            count_below_up = sum(1 for s in scores if s < candidate_up)
            lines.append(
                f"- At threshold **{candidate_up:.4f}** (+{delta:.2f}): **{count_below_up}** "
                f"{'service would be suspicious' if count_below_up == 1 else 'services would be suspicious'}."
            )
        lines.append("")

        # Find natural gaps
        sorted_scores = sorted(scores)
        gaps = [
            (sorted_scores[i], sorted_scores[i + 1], sorted_scores[i + 1] - sorted_scores[i])
            for i in range(len(sorted_scores) - 1)
        ]
        max_gap = max(gaps, key=lambda x: x[2]) if gaps else None
        if max_gap and max_gap[2] >= 0.1:
            lines.append(
                f"A natural gap of **{max_gap[2]:.4f}** exists between "
                f"**{max_gap[0]:.4f}** and **{max_gap[1]:.4f}**, "
                f"suggesting the current threshold is "
                f"{'well-placed' if threshold and max_gap[0] < threshold < max_gap[1] else 'adjustable'}.\n"
            )

    # ── Data Sources ──────────────────────────────────────────────────────
    lines.append("---\n")
    lines.append("### Data Sources")
    if traces_count:
        lines.append(f"- **Traces analysed:** {traces_count}")
    if spans_count:
        lines.append(f"- **Spans analysed:** {spans_count}")
    method_str = scom_method or "paper"
    lines.append(f"- **SCOM method:** {method_str}")
    lines.append("")

    return "\n".join(lines)


def generate_narrative_analysis(
    rank_path: Path,
    mapping_path: Path,
    data_dir: Path | None = None,
) -> str | None:
    """Generate a narrative analysis of SCOM results using the LLM.

    Args:
        rank_path: Path to service_rank.csv.
        mapping_path: Path to endpoint_table_map.csv.
        data_dir: Optional base directory for additional context.

    Returns:
        Markdown-formatted analysis text, or None if the LLM call fails.
    """
    if not rank_path.exists() or not mapping_path.exists():
        return None

    try:
        rank_df = pd.read_csv(rank_path)
        mapping_df = pd.read_csv(mapping_path)
    except (OSError, PermissionError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
        logger.warning("Could not read rank/mapping CSV: %s", e)
        return None

    if rank_df.empty or mapping_df.empty:
        return None

    rank_csv_str = rank_df.to_string(index=False)
    mapping_csv_str = mapping_df.to_string(index=False)

    resolved_data_dir = data_dir or rank_path.parent.parent
    context_text = _find_project_context(resolved_data_dir)

    # Count spans and traces for data provenance
    spans_count = 0
    traces_count = 0
    spans_path = resolved_data_dir / "interim" / "spans.csv"
    if spans_path.exists():
        try:
            spans_df = pd.read_csv(spans_path)
            spans_count = len(spans_df)
            if "trace_id" in spans_df.columns:
                traces_count = spans_df["trace_id"].nunique()
        except (OSError, PermissionError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logger.warning("Could not read spans CSV: %s", e)

    # Add SCOM method description to context
    scom_method = ""
    if "method" in rank_df.columns:
        methods = sorted({str(m) for m in rank_df["method"].dropna().unique()})
        if methods:
            scom_method = f"SCOM method(s): {', '.join(methods)}"
            if not context_text.startswith("Project context not available"):
                context_text += f"\n{scom_method}"
            else:
                context_text = scom_method

    prompt = build_analysis_prompt(
        rank_csv_str,
        mapping_csv_str,
        context_text,
        spans_count=spans_count,
        traces_count=traces_count,
    )

    result = call_llm(prompt, temperature=0.3, max_tokens=4000)

    if result is not None:
        return result

    # Fallback: local computed analysis when LLM is unavailable
    try:
        return _generate_local_analysis(rank_df, mapping_df, spans_count, traces_count, scom_method)
    except Exception as e:
        logger.exception("Local analysis failed: %s", e)
        return None
