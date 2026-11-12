"""PDF report of a recommendation result (reportlab, built-in fonts only)."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#1f241f")
MUTED = colors.HexColor("#5d675c")
ACCENT = colors.HexColor("#b4592f")
RULE = colors.HexColor("#c9cdbf")
TINT = colors.HexColor("#f1efe6")
SEV = {"critical": colors.HexColor("#a3321f"), "warning": colors.HexColor("#8a5a12"), "info": MUTED}

_CORE = ("frontend", "backend", "database", "hosting")


def _t(v):
    """Escape for Paragraph markup and keep to the Latin-1 range the built-in fonts cover."""
    s = escape(str(v if v is not None else ""))
    for a, b in (("→", "->"), ("–", "-"), ("—", "-"), ("‘", "'"), ("’", "'"),
                 ("“", '"'), ("”", '"'), ("₹", "INR "), ("≥", ">="), ("≤", "<="),
                 ("•", "-"), ("…", "...")):
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _styles():
    base = dict(fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT)
    return {
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8.5, "leading": 11.5, "textColor": MUTED}),
        "title": ParagraphStyle("title", **{**base, "fontName": "Times-Roman", "fontSize": 28, "leading": 32}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Times-Roman", "fontSize": 17, "leading": 21, "spaceBefore": 14, "spaceAfter": 5}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 10.5, "leading": 14, "spaceBefore": 8, "spaceAfter": 2}),
        "eyebrow": ParagraphStyle("eyebrow", **{**base, "fontName": "Helvetica-Bold", "fontSize": 7.5, "leading": 10, "textColor": ACCENT}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 11.5}),
        "cellb": ParagraphStyle("cellb", **{**base, "fontName": "Helvetica-Bold", "fontSize": 8.5, "leading": 11.5}),
    }


def _table(rows, widths, st, header=True):
    data = [[Paragraph(_t(c), st["cellb" if (header and i == 0) else "cell"]) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), TINT))
    t.setStyle(TableStyle(style))
    return t


def _bullets(items, st):
    return [Paragraph("- " + _t(i), st["body"]) for i in items]


def _component(layer, comp, st):
    if not isinstance(comp, dict):
        return []
    out = [Paragraph("%s: %s" % (_t(layer.replace("_", " ").title()), _t(comp.get("choice", "None"))), st["h2"]),
           Paragraph(_t(comp.get("justification", "")), st["body"])]
    if comp.get("driven_by"):
        out.append(Paragraph("Driven by: " + _t(", ".join(comp["driven_by"])), st["small"]))
    for alt in comp.get("alternatives") or []:
        out.append(Paragraph("<b>Not %s:</b> %s" % (_t(alt.get("choice", "")), _t(alt.get("why_not", ""))), st["body"]))
    for tr in comp.get("tradeoffs") or []:
        out.append(Paragraph("Trade-off: " + _t(tr), st["small"]))
    return [KeepTogether(out)]


def _usd(v):
    return "$%s" % format(round(v), ",") if isinstance(v, (int, float)) else _t(v)


def _int(v):
    return format(v, ",") if isinstance(v, int) and not isinstance(v, bool) else v


def _num(v, nd=3):
    return ("%.*f" % (nd, v)) if isinstance(v, (int, float)) else _t(v)


def _calculations(r, st, width):
    """Ranking, weights, score breakdown and elimination summary taken from the engine's trace."""
    out = []
    ranking = r.get("ranking")
    if isinstance(ranking, list) and ranking:
        rows = [["#", "Frontend", "Backend", "Database", "Hosting", "Score", "Core cost / month"]]
        for x in ranking:
            s = x.get("stack") or {}
            c = x.get("cost_usd")
            rows.append([x.get("rank", ""), s.get("frontend", ""), s.get("backend", ""), s.get("database", ""),
                         s.get("hosting", ""), _num(x.get("score"), 2),
                         "%s to %s" % (_usd(c[0]), _usd(c[1])) if isinstance(c, (list, tuple)) and len(c) == 2 else ""])
        out.append(Paragraph("Ranking of the top stacks", st["h1"]))
        if len(ranking) > 1 and all(isinstance(ranking[i].get("score"), (int, float)) for i in (0, 1)):
            out.append(Paragraph("The winner leads the runner-up by %.2f points." % (ranking[0]["score"] - ranking[1]["score"]), st["body"]))
        out += [
                _table(rows, [8 * mm, 36 * mm, 28 * mm, 28 * mm, 28 * mm, 14 * mm, width - 142 * mm], st),
                Paragraph("Cost in this table covers the four core layers only; supporting services are added in the "
                          "monthly cost estimate above.", st["small"])]
    trace = r.get("decision_trace")
    trace = trace if isinstance(trace, dict) else {}
    score = r.get("score")
    if isinstance(score, dict) and score:
        out.append(Paragraph("Score breakdown of the chosen stack", st["h1"]))
        if trace.get("score_formula"):
            out.append(Paragraph(_t(trace["score_formula"]), st["small"]))
        rows = [["Part", "Value"], ["Total", _num(score.get("total"))], ["Quality (Q)", _num(score.get("quality"))],
                ["Fit (F)", _num(score.get("fit"))], ["Penalties", _num(score.get("penalty"))]]
        out.append(_table(rows, [50 * mm, 30 * mm], st))
        for title, key in (("Quality criteria (0-10)", "criteria"), ("Fit terms (0-1)", "fit_terms")):
            d = score.get(key)
            if isinstance(d, dict) and d:
                rows = [[title, "Value"]] + [[k.replace("_", " ").capitalize(), _num(v, 2)] for k, v in d.items()]
                out += [Spacer(1, 6), _table(rows, [50 * mm, 30 * mm], st)]
        pen = [(k, v) for k, v in (score.get("penalties") or {}).items() if isinstance(v, (int, float)) and abs(v) > 1e-9]
        if pen:
            rows = [["Penalty applied", "Points"]] + [[k.replace("_", " ").capitalize(), _num(v, 2)] for k, v in pen]
            out += [Spacer(1, 6), _table(rows, [50 * mm, 30 * mm], st)]
    for title, key in (("Criterion weights (after context adjustment)", "weights"), ("Layer weights in the stack score", "layer_weights")):
        d = trace.get(key)
        if isinstance(d, dict) and d:
            rows = [["Name", "Weight"]] + [[k.replace("_", " ").capitalize(), _num(v)] for k, v in d.items()]
            out += [Paragraph(title, st["h2"]), _table(rows, [50 * mm, 30 * mm], st)]
    layers = trace.get("layers")
    if isinstance(layers, dict) and layers:
        out += [Paragraph("Eliminated candidates", st["h1"]),
                Paragraph("Options removed by hard constraints before scoring, with the reasons the engine recorded.", st["small"])]
        for layer, info in layers.items():
            if not isinstance(info, dict):
                continue
            merged = {}
            for e in info.get("eliminated") or []:
                if isinstance(e, dict):
                    merged.setdefault(e.get("name", e.get("id", "")), []).extend(e.get("reasons") or [])
            head = "%s: %s candidates, %s eliminated, %s left after compatibility checks" % (
                str(layer).title(), info.get("candidates", "?"), len(merged), info.get("eligible_after_stack_checks", "?"))
            out.append(Paragraph(_t(head), st["h2"]))
            if merged:
                rows = [["Candidate", "Reason"]] + [[n, "; ".join(dict.fromkeys(rs))] for n, rs in merged.items()]
                out.append(_table(rows, [50 * mm, width - 50 * mm], st))
    ss = trace.get("stack_search")
    if isinstance(ss, dict) and ss:
        rows = [["Combinations", "Count"], ["Considered", _int(ss.get("considered", ""))], ["Scored", _int(ss.get("scored", ""))],
                ["Removed by hard budget", _int(ss.get("removed_by_hard_budget", ""))]]
        rows += [["Pruned: " + k, _int(v)] for k, v in (ss.get("pruned_by_reason") or {}).items()]
        out += [Paragraph("Stack search", st["h1"]), _table(rows, [width - 30 * mm, 30 * mm], st)]
    return out



def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "ArchLens - StackSense engine")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def build_pdf(result):
    """Return PDF bytes for an engine result dict (also handles invalid or infeasible results)."""
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title="ArchLens recommendation", author="ArchLens")
    width = A4[0] - 36 * mm
    r = result if isinstance(result, dict) else {}
    story = [Paragraph("ARCHLENS - STACKSENSE ENGINE %s" % _t(r.get("engine_version", "")), st["eyebrow"]), Spacer(1, 4),
             Paragraph("Technology stack recommendation", st["title"]), Spacer(1, 4),
             HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=4, spaceAfter=6)]
    meta = [["Scenario", r.get("scenario_id") or "-"], ["Status", str(r.get("status", "-")).replace("_", " ")],
            ["Architecture style", str(r.get("architecture_style") or "-").replace("_", " ")]]
    story.append(_table(meta, [38 * mm, width - 38 * mm], st, header=False))
    if r.get("summary"):
        story += [Paragraph("Summary", st["h1"]), Paragraph(_t(r["summary"]), st["body"])]

    stack = r.get("stack")
    if isinstance(stack, dict):
        rows = [["Layer", "Choice"]] + [[k.title(), (stack.get(k) or {}).get("choice", "None")] for k in _CORE]
        for s in stack.get("supporting") or []:
            rows.append([str(s.get("layer", "")).replace("_", " ").title(), s.get("choice", "")])
        story += [Paragraph("Recommended stack", st["h1"]), _table(rows, [38 * mm, width - 38 * mm], st)]
        story.append(Paragraph("Reasoning", st["h1"]))
        for k in _CORE:
            story += _component(k, stack.get(k), st)
        if stack.get("supporting"):
            story.append(Paragraph("Supporting services", st["h1"]))
            for s in stack["supporting"]:
                story += _component(s.get("layer", "service"), s, st)
    else:
        story += [Paragraph("No stack", st["h1"]),
                  Paragraph("The engine did not produce a stack for this input. The reasons are listed below.", st["body"])]

    cost = r.get("estimated_monthly_cost_usd")
    if isinstance(cost, dict):
        story.append(Paragraph("Estimated monthly cost", st["h1"]))
        story.append(Paragraph("%s to %s per month (order of magnitude, not a quote)" % (_usd(cost.get("min")), _usd(cost.get("max"))), st["body"]))
        cr = cost.get("credits")
        if isinstance(cr, dict) and cr.get("covered_usd"):
            line = "%s credits cover about %s to %s of this per month" % (_t(cr.get("provider", "")), _usd(cr["covered_usd"][0]), _usd(cr["covered_usd"][1]))
            fy = cr.get("first_year_average_usd")
            line += ("; first-year average after credits: %s to %s per month." % (_usd(fy[0]), _usd(fy[1]))) if fy else "."
            story.append(Paragraph(line, st["body"]))
        story += _bullets(cost.get("assumptions") or [], st)

    rob = r.get("robustness")
    if isinstance(rob, dict) and rob.get("checks"):
        story.append(Paragraph("How firm the pick is", st["h1"]))
        story.append(Paragraph("%s: %s" % (_t(rob.get("verdict", "")).capitalize(), _t(rob.get("summary", ""))), st["body"]))
        rows = [["Change", "Result"]]
        for c in rob["checks"]:
            if c.get("same_stack"):
                res = "Same stack"
            else:
                res = c.get("result") or "; ".join("%s: %s to %s" % (w.get("layer", ""), w.get("from", ""), w.get("to", "")) for w in c.get("swaps") or []) or "Different stack"
            rows.append([c.get("change", ""), res])
        story.append(_table(rows, [70 * mm, width - 70 * mm], st))

    opts = r.get("trade_off_options")
    if isinstance(opts, list) and opts:
        story.append(Paragraph("If you want something different", st["h1"]))
        story += _bullets([o.get("summary", "") for o in opts if o.get("summary")], st)

    fixes = r.get("repairs")
    if isinstance(fixes, list) and fixes:
        story.append(Paragraph("Single changes that make a stack possible", st["h1"]))
        rows = [["Change", "Resulting stack", "Core cost / month"]]
        for f in fixes:
            s = f.get("stack") or {}
            c = f.get("cost_usd") or [None, None]
            rows.append([f.get("change", ""), ", ".join(str(v) for v in s.values()) if isinstance(s, dict) else str(s),
                         "%s to %s" % (_usd(c[0]), _usd(c[1])) if isinstance(c, (list, tuple)) and len(c) == 2 else ""])
        story.append(_table(rows, [60 * mm, width - 100 * mm, 40 * mm], st))

    if r.get("warnings"):
        story.append(Paragraph("Warnings", st["h1"]))
        rows = [["Severity", "Code", "Message"]] + [[w.get("severity", ""), w.get("code", ""), w.get("message", "")] for w in r["warnings"]]
        story.append(_table(rows, [22 * mm, 42 * mm, width - 64 * mm], st))
    if r.get("validation_errors"):
        story.append(Paragraph("Validation errors", st["h1"]))
        story.append(_table([["Field", "Message"]] + [[e.get("field", ""), e.get("message", "")] for e in r["validation_errors"]],
                            [50 * mm, width - 50 * mm], st))
    if r.get("assumptions"):
        story.append(Paragraph("Assumptions", st["h1"]))
        story += _bullets(r["assumptions"], st)

    story += _calculations(r, st, width)
    ai = r.get("ai_narrative")
    if isinstance(ai, dict) and ai.get("text"):
        story += [Paragraph("Plain-language rewording (optional, model-written)", st["h1"]), Paragraph(_t(ai["text"]), st["body"]),
                  Paragraph("The stack above was chosen by the rule engine; this text only rephrases it.", st["small"])]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
