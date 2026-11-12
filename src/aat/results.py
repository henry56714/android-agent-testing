from __future__ import annotations

import fnmatch
import re
import xml.etree.ElementTree as ET


def xml_root(path):
    data = path.read_bytes()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entities/DTD are not supported")
    return ET.fromstring(data)


def junit(paths, selector=None):
    records = []
    for path in paths:
        root = xml_root(path)
        if root.tag not in ("testsuite", "testsuites"):
            raise ValueError("Not a JUnit report")
        for suite in root.iter("testsuite"):
            if "tests" in suite.attrib and not list(suite.iter("testsuite"))[1:]:
                if int(suite.attrib["tests"]) != len(suite.findall("testcase")):
                    raise ValueError("Incomplete JUnit suite: declared test count mismatch")
        for t in root.iter("testcase"):
            name = f"{t.get('classname', '')}#{t.get('name', '')}"
            outcome = "PASS"
            # Structured JUnit elements take precedence over advisory status.
            # Maestro 2.12 emits status=ERROR with a <failure> for assertions.
            if t.find("error") is not None:
                outcome = "ERROR"
            elif t.find("failure") is not None:
                outcome = "FAIL"
            elif t.find("skipped") is not None:
                outcome = "SKIPPED"
            elif t.get("status", "").lower() in ("error", "errored"):
                outcome = "ERROR"
            elif t.get("status", "").lower() in (
                "failure",
                "failed",
                "fail",
            ):
                outcome = "FAIL"
            elif t.get("status", "").lower() in (
                "skipped",
                "disabled",
                "notrun",
            ):
                outcome = "SKIPPED"
            records.append({"selector": name, "outcome": outcome})
    selected = [r for r in records if selector is None or fnmatch.fnmatchcase(r["selector"], selector)]
    if not selected:
        return "ERROR", "TEST_NOT_DISCOVERED", []
    outcomes = {r["outcome"] for r in selected}
    outcome = next(x for x in ("ERROR", "FAIL", "SKIPPED", "PASS") if x in outcomes)
    # Do not ignore a failure elsewhere in the invoked task.
    if any(r["outcome"] in ("FAIL", "ERROR") for r in records) and outcome == "PASS":
        return "ERROR", "UNSELECTED_TEST_FAILURE", selected
    return outcome, "ASSERTIONS_EVALUATED", selected


def lint(paths, severities):
    count = 0
    for path in paths:
        root = xml_root(path)
        if root.tag != "issues":
            raise ValueError("Not an Android Lint XML report")
        count += sum(i.get("severity") in severities for i in root.findall("issue"))
    return ("FAIL" if count else "PASS"), "LINT_POLICY", [{"violations": count}]


def instrumentation(text, selector):
    """Require terminal OK AND completed matching test status; adb exit 0 is insufficient."""
    if "INSTRUMENTATION_FAILED" in text or "INSTRUMENTATION_ABORTED" in text:
        return "ERROR", "INSTRUMENTATION_ABORTED", []
    records, current = [], {}
    for line in text.splitlines():
        if line.startswith("INSTRUMENTATION_STATUS: "):
            key, _, value = line.removeprefix("INSTRUMENTATION_STATUS: ").partition("=")
            current[key] = value
        elif line.startswith("INSTRUMENTATION_STATUS_CODE: "):
            code = int(line.split(": ", 1)[1])
            if code <= 0 and "class" in current and "test" in current:
                records.append(
                    {
                        "selector": current["class"] + "#" + current["test"],
                        "outcome": {0: "PASS", -2: "FAIL", -3: "SKIPPED", -4: "SKIPPED"}.get(code, "ERROR"),
                    }
                )
            current = {}
    matches = [r for r in records if fnmatch.fnmatchcase(r["selector"], selector)]
    if not matches:
        return "ERROR", "TEST_NOT_DISCOVERED", records
    for outcome in ("ERROR", "FAIL", "SKIPPED"):
        if any(r["outcome"] == outcome for r in records):
            return outcome, "ASSERTIONS_EVALUATED", records
    if not re.search(r"OK \([1-9]\d* tests?\)", text) or "INSTRUMENTATION_CODE: -1" not in text:
        return "ERROR", "INCOMPLETE_INSTRUMENTATION", records
    return "PASS", "ASSERTIONS_EVALUATED", records


def gate(cases):
    required = [c for c in cases if c.get("required", True)]
    if not required:
        return "not_passed", 2
    outcomes = {c["outcome"] for c in required}
    for outcome, code in (("CANCELLED", 130), ("ERROR", 4), ("BLOCKED", 3), ("FAIL", 1), ("SKIPPED", 3)):
        if outcome in outcomes:
            return "not_passed", code
    return ("passed", 0) if outcomes == {"PASS"} else ("not_passed", 4)


def export_report(directory, result):
    from .storage import write_json

    write_json(directory / "result.json", result)
    lines = [
        f"# Run {result['run_id']}",
        "",
        f"Gate: {result['gate']}",
        "",
        f"Scope: {result.get('coverage', {}).get('scope', 'partial')}",
        f"Full project passed: {result.get('coverage', {}).get('full_project_passed', False)}",
        "",
        "| Case | Revision | Outcome | Reason |",
        "|---|---|---|---|",
    ]
    root = ET.Element("testsuite", name=result["project_id"], tests=str(len(result["cases"])))
    for c in result["cases"]:
        lines.append(f"| {c['id']} | {c['revision']} | {c['outcome']} | {c['reason_code']} |")
        t = ET.SubElement(root, "testcase", name=c["id"], classname=result["project_id"])
        if c["outcome"] != "PASS":
            tag = "failure" if c["outcome"] == "FAIL" else "skipped" if c["outcome"] == "SKIPPED" else "error"
            ET.SubElement(t, tag, message=c["outcome"] + ": " + c["reason_code"])
    for name, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
        root.set(name, str(len(root.findall(f"testcase/{tag}"))))
    coverage = result.get("coverage", {})
    if coverage.get("unresolved") or coverage.get("excluded"):
        lines.extend(["", "## Discovery audit", ""])
        for item in coverage.get("unresolved", []):
            lines.append(f"- Unresolved: {item['item']} — {item['reason']}")
        for item in coverage.get("excluded", []):
            lines.append(f"- Excluded: {item['item']} — {item['reason']}")
    ET.ElementTree(root).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
    (directory / "report.md").write_text("\n".join(lines) + "\n")
