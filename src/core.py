"""Desktop portal engine for SigmaOS-desktop-portal (xdg-desktop-portal-xapp).

Principles: backend config is parsed into a portal->backend map with
conflict detection (one portal, one backend), required-portal coverage is
reported explicitly, and every request builder (screenshot, FileChooser,
RemoteDesktop) is a pure plan validated before any D-Bus call happens.
"""
import argparse, json, os, re, sys

ENGINE = {
    "name": "sigma-portal",
    "summary": "Plan xdg-desktop-portal requests and audit backends",
    "language": "python",
    "verbs": ["portals", "screenshot-plan", "files", "remote"],
    "principles": ["Separation of Concerns", "Plan Before Act",
                   "Defensive Programming", "Fail-Fast", "KISS", "DRY",
                   "Deterministic Testing"],
}

cli_doc = """\
$ sigma-portal portals --config-dir /etc/xdg-desktop-portal
backends: 2   portals: 12   missing: none
$ sigma-portal screenshot-plan --interactive --delay 2
Screenshot(parent, {"interactive": true, "delay": 2})
$ sigma-portal files --mode open --multiple --mime image/png image/jpeg
OpenFile(parent, {"multiple": true, ...})
$ sigma-portal remote --actions pointer keyboard --persist
"""

_PORTAL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_BACKEND_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MIME_RE = re.compile(r"^(?:\*|[a-z0-9.+-]+)/(?:\*|[a-z0-9.+-]+)$")
_ACTIONS = ("pointer", "keyboard", "clipboard", "screen", "pause")
REQUIRED_PORTALS = ("AppChooser", "FileChooser", "GlobalShortcuts",
                    "NetworkMonitor", "Notification", "OpenURI", "Print",
                    "ProxyResolver", "RemoteDesktop", "ScreenCast",
                    "Screenshot", "Settings")


class PortalError(ValueError):
    pass


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def parse_portal_config(text, source="<text>"):
    """Parse one .portal file: [portal] section with Portal=backend keys."""
    mapping, section = {}, None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section != "portal":
                raise PortalError(f"{source}:{lineno}: unknown section "
                                  f"{section!r} (want [portal])")
            continue
        if section != "portal" or "=" not in line:
            raise PortalError(f"{source}:{lineno}: cannot parse {line!r}")
        key, _, backend = line.partition("=")
        portal, backend = key.strip(), backend.strip()
        if not _PORTAL_NAME_RE.match(portal):
            raise PortalError(f"{source}:{lineno}: bad portal name "
                              f"{portal!r}")
        if not _BACKEND_RE.match(backend):
            raise PortalError(f"{source}:{lineno}: bad backend {backend!r}")
        if portal in mapping:
            raise PortalError(f"{source}:{lineno}: duplicate portal "
                              f"{portal!r} in one file")
        mapping[portal] = backend
    if not mapping:
        raise PortalError(f"{source}: no portals declared")
    return mapping


def merge_configs(configs):
    """Merge {filename: text} into one map; cross-file conflicts fail."""
    merged, origin = {}, {}
    for source in sorted(configs):
        for portal, backend in parse_portal_config(configs[source],
                                                   source=source).items():
            if portal in merged and merged[portal] != backend:
                raise PortalError(
                    f"portal {portal!r} claimed by two backends: "
                    f"{merged[portal]!r} ({origin[portal]}) vs "
                    f"{backend!r} ({source})")
            merged[portal] = backend
            origin[portal] = source
    return merged


def coverage(merged):
    """Report which required portals are missing."""
    missing = sorted(set(REQUIRED_PORTALS) - set(merged))
    extra = sorted(set(merged) - set(REQUIRED_PORTALS))
    return {"total": len(merged), "missing": missing, "extra": extra,
            "complete": not missing}


def screenshot_plan(interactive=True, delay=0, directory=None):
    """Pure Screenshot.Screenshot request plan (portal version 2+)."""
    if isinstance(delay, bool) or not isinstance(delay, int) \
            or not 0 <= delay <= 300:
        raise PortalError(f"delay must be 0..300 seconds, got {delay!r}")
    if not interactive and not directory:
        raise PortalError(
            "non-interactive screenshots need an explicit --directory")
    params = {"interactive": bool(interactive), "modal": True}
    if delay:
        params["delay"] = delay
    steps = [{"method": "org.freedesktop.portal.Screenshot.Screenshot",
              "parent_window": "", "params": params}]
    if delay:
        steps.append({"wait_seconds": delay})
    steps.append({"watch_signal":
                  "org.freedesktop.portal.Session::Response"})
    if interactive:
        target = "<user-chosen path>"
    else:
        target = os.path.join(directory, "screenshot-<timestamp>.png")
    return {"target": target, "steps": steps}


def files_plan(mode="open", multiple=False, mimes=None, current=None):
    """Pure FileChooser request plan with filter validation."""
    if mode not in ("open", "save"):
        raise PortalError(f"mode must be open|save, got {mode!r}")
    if multiple and mode == "save":
        raise PortalError("SaveFile cannot be multiple")
    mimes = list(mimes or [])
    for mime in mimes:
        if not _MIME_RE.match(mime):
            raise PortalError(f"bad mime filter {mime!r}")
    if len(set(mimes)) != len(mimes):
        raise PortalError("duplicate mime filter")
    if current is not None and current not in mimes:
        raise PortalError(f"current filter {current!r} not in filter list")
    method = ("org.freedesktop.portal.FileChooser.OpenFile"
              if mode == "open" else
              "org.freedesktop.portal.FileChooser.SaveFile")
    params = {"multiple": bool(multiple),
              "filters": [[m, []] for m in mimes]}
    if current is not None:
        params["current_filter"] = [current, []]
    return {"method": method,
            "steps": [{"method": method, "parent_window": "",
                       "params": params},
                      {"watch_signal":
                       "org.freedesktop.portal.Session::Response"}]}


def remote_plan(actions, persist=False):
    """Pure RemoteDesktop SelectDevices/Start plan."""
    wanted = list(actions)
    if not wanted:
        raise PortalError("no remote desktop actions requested")
    unknown = sorted(set(wanted) - set(_ACTIONS))
    if unknown:
        raise PortalError(f"unknown action(s): {', '.join(unknown)} "
                          f"(available: {', '.join(_ACTIONS)})")
    if len(set(wanted)) != len(wanted):
        raise PortalError("duplicate action requested")
    selected = sorted(wanted)
    return {"actions": selected, "persist": bool(persist), "steps": [
        {"method": "org.freedesktop.portal.RemoteDesktop.SelectDevices",
         "params": {"types": selected, "persist": bool(persist)}},
        {"method": "org.freedesktop.portal.RemoteDesktop.Start",
         "params": {"session_handle_token": "sigmaos"}},
        {"watch_signal":
         "org.freedesktop.portal.Session::Response"},
    ]}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"],
                                description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    po = sub.add_parser("portals", help="audit portal backend coverage")
    po.add_argument("--config-dir", required=True)
    sc = sub.add_parser("screenshot-plan", help="plan a screenshot request")
    sc.add_argument("--interactive", action="store_true")
    sc.add_argument("--no-interactive", dest="interactive",
                    action="store_false")
    sc.set_defaults(interactive=True)
    sc.add_argument("--delay", type=int, default=0)
    sc.add_argument("--directory", default=None)
    fl = sub.add_parser("files", help="plan a FileChooser request")
    fl.add_argument("--mode", choices=["open", "save"], default="open")
    fl.add_argument("--multiple", action="store_true")
    fl.add_argument("--mime", nargs="*", default=[], dest="mimes")
    fl.add_argument("--current", default=None)
    rm = sub.add_parser("remote", help="plan a RemoteDesktop request")
    rm.add_argument("--actions", nargs="+", default=[], dest="actions")
    rm.add_argument("--persist", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "portals":
            if not os.path.isdir(args.config_dir):
                raise PortalError(f"no such config dir: {args.config_dir!r}")
            configs = {}
            for name in sorted(os.listdir(args.config_dir)):
                if name.endswith(".portal"):
                    path = os.path.join(args.config_dir, name)
                    configs[path] = _read(path)
            if not configs:
                raise PortalError(f"no .portal files in {args.config_dir!r}")
            merged = merge_configs(configs)
            report = coverage(merged)
            missing = ", ".join(report["missing"]) or "none"
            print(f"backends: {len(set(merged.values()))}   "
                  f"portals: {report['total']}   missing: {missing}")
            for portal in sorted(merged):
                print(f"  {portal:<18} -> {merged[portal]}")
            return 0 if report["complete"] else 1
        if args.cmd == "screenshot-plan":
            plan = screenshot_plan(interactive=args.interactive,
                                   delay=args.delay,
                                   directory=args.directory)
            print(f"target: {plan['target']}")
            for step in plan["steps"]:
                print(json.dumps(step, sort_keys=True))
        elif args.cmd == "files":
            plan = files_plan(mode=args.mode, multiple=args.multiple,
                              mimes=args.mimes, current=args.current)
            for step in plan["steps"]:
                print(json.dumps(step, sort_keys=True))
        elif args.cmd == "remote":
            plan = remote_plan(args.actions, persist=args.persist)
            print(f"actions: {', '.join(plan['actions'])}  "
                  f"persist: {plan['persist']}")
            for step in plan["steps"]:
                print(json.dumps(step, sort_keys=True))
        return 0
    except PortalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


_GOOD_A = """\
# xapp backend
[portal]
Screenshot=xapp
ScreenshotChooser=xapp
FileChooser=xapp
OpenURI=xapp
Notification=xapp
Settings=xapp
RemoteDesktop=xapp
"""
_GOOD_B = """\
[portal]
ScreenCast=gnome
Print=gnome
NetworkMonitor=gnome
ProxyResolver=gnome
AppChooser=gnome
GlobalShortcuts=gnome
"""


def self_test() -> None:
    import tempfile
    merged = merge_configs({"xapp.portal": _GOOD_A, "gnome.portal": _GOOD_B})
    assert merged["Screenshot"] == "xapp"
    assert merged["ScreenCast"] == "gnome"
    report = coverage(merged)
    assert report["complete"] is True
    assert report["missing"] == []
    assert report["total"] == 13

    # cross-backend conflict
    try:
        merge_configs({"xapp.portal": _GOOD_A,
                       "conflict.portal": "[portal]\nScreenshot=gnome\n"})
    except PortalError as e:
        assert "two backends" in str(e)
    else:
        raise AssertionError("conflicting portal accepted")
    # incomplete coverage is reported, not fatal
    partial = coverage(merge_configs({"xapp.portal": _GOOD_A}))
    assert partial["complete"] is False
    assert "ScreenCast" in partial["missing"] and \
        "Print" in partial["missing"]
    # single-file validation
    for bad, frag in (
        ("Portal=xapp\n", "cannot parse"),
        ("[wrong]\nPortal=xapp\n", "unknown section"),
        ("[portal]\nBad Name=xapp\n", "bad portal name"),
        ("[portal]\nPortal=BAD BACKEND\n", "bad backend"),
        ("[portal]\nP=xapp\nP=gnome\n", "duplicate portal"),
        ("# nothing\n", "no portals declared"),
    ):
        try:
            parse_portal_config(bad, source="bad.portal")
        except PortalError as e:
            assert frag in str(e), (frag, str(e))
        else:
            raise AssertionError(f"config accepted: {frag}")

    # screenshot plans
    plan = screenshot_plan(interactive=True, delay=2)
    assert plan["steps"][0]["method"].endswith("Screenshot.Screenshot")
    assert plan["steps"][0]["params"] == {"interactive": True,
                                          "modal": True, "delay": 2}
    assert plan["steps"][1] == {"wait_seconds": 2}
    assert plan["target"] == "<user-chosen path>"
    plan = screenshot_plan(interactive=False, directory="/tmp/shots")
    assert plan["target"] == "/tmp/shots/screenshot-<timestamp>.png"
    assert "delay" not in plan["steps"][0]["params"]
    for bad in (lambda: screenshot_plan(delay=301),
                lambda: screenshot_plan(delay=-1),
                lambda: screenshot_plan(interactive=False),
                lambda: screenshot_plan(delay=True)):   # bool is not int
        try:
            bad()
        except PortalError:
            pass
        else:
            raise AssertionError("bad screenshot plan accepted")

    # FileChooser plans
    plan = files_plan(mode="open", multiple=True,
                      mimes=["image/png", "image/jpeg"],
                      current="image/png")
    assert plan["method"].endswith("FileChooser.OpenFile")
    params = plan["steps"][0]["params"]
    assert params["multiple"] is True
    assert params["filters"] == [["image/png", []], ["image/jpeg", []]]
    assert params["current_filter"] == ["image/png", []]
    assert files_plan(mode="save")["method"].endswith("SaveFile")
    for bad in (lambda: files_plan(mode="print"),
                lambda: files_plan(mode="save", multiple=True),
                lambda: files_plan(mimes=["not-a-mime"]),
                lambda: files_plan(mimes=["image/png", "image/png"]),
                lambda: files_plan(mimes=["image/png"], current="video/mp4")):
        try:
            bad()
        except PortalError:
            pass
        else:
            raise AssertionError("bad files plan accepted")

    # RemoteDesktop plans
    plan = remote_plan(["keyboard", "pointer"], persist=True)
    assert plan["actions"] == ["keyboard", "pointer"]
    select = plan["steps"][0]["params"]
    assert select == {"types": ["keyboard", "pointer"], "persist": True}
    assert plan["steps"][1]["method"].endswith("RemoteDesktop.Start")
    for bad in ([], ["pointer", "pointer"], ["teleport"],
                ["pointer", "teleport"]):
        try:
            remote_plan(bad)
        except PortalError:
            pass
        else:
            raise AssertionError(f"bad remote plan accepted: {bad}")

    # config-dir scanning (tempdir) via the same merge logic main() uses
    with tempfile.TemporaryDirectory() as d:
        for name, body in (("xapp.portal", _GOOD_A),
                           ("gnome.portal", _GOOD_B)):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)
        configs = {os.path.join(d, n): open(os.path.join(d, n)).read()
                   for n in sorted(os.listdir(d))
                   if n.endswith(".portal")}
        assert len(configs) == 2
        assert coverage(merge_configs(configs))["complete"] is True
        with open(os.path.join(d, "notes.txt"), "w") as fh:
            fh.write("ignored by the scanner")
        configs = {os.path.join(d, n): open(os.path.join(d, n)).read()
                   for n in sorted(os.listdir(d))
                   if n.endswith(".portal")}
        assert len(configs) == 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
