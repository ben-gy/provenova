"""`ql` command-line interface."""

from __future__ import annotations

import importlib
import json
import os

import typer
from rich.console import Console
from rich.table import Table

from ..agent import CaptureAgent, capture
from ..config import default_home, load_config, save_config
from ..registry import extra_for, registry
from ..store import LocalLedger

app = typer.Typer(add_completion=False, help="Provenova client — capture, reproduce, publish.")
console = Console()


def _ledger() -> LocalLedger:
    return LocalLedger(load_config().db_url)


@app.command()
def init():
    """Create the local ledger + config (no account, offline)."""
    cfg = load_config()
    LocalLedger(cfg.db_url)  # init_db
    save_config(cfg)
    console.print(f"[green]Initialized[/] Provenova at [bold]{default_home()}[/]")
    console.print(f"  store: {cfg.store_path}")


@app.command()
def demo(project: str = "demo", shots: int = 2048, qubits: int = 3):
    """Run a built-in GHZ capture against a local simulator."""
    try:
        from qiskit import QuantumCircuit
        from qiskit_aer import AerSimulator
    except ModuleNotFoundError:
        console.print("[red]The demo needs the Aer simulator.[/] Install it with:")
        console.print('  [bold]pip install "provenova[aer]"[/]')
        raise typer.Exit(1)

    ledger = _ledger()

    @capture(project=project, agent=CaptureAgent(ledger), shots=shots)
    def _run():
        qc = QuantumCircuit(qubits)
        qc.h(0)
        for i in range(qubits - 1):
            qc.cx(i, i + 1)
        qc.measure_all()
        backend = AerSimulator()
        return backend.run(qc, shots=shots)

    _run()
    rows = ledger.list_runs(limit=1)
    if rows:
        console.print(f"[green]Recorded[/] run [bold]{rows[0]['id']}[/] "
                      f"({rows[0]['backend']}, {rows[0]['shots']} shots)")
        console.print(f"  run_hash: {rows[0]['run_hash']}")


@app.command()
def capture_target(target: str = typer.Argument(..., help="module:function to run under capture"),
                   project: str = typer.Option(None), shots: int = typer.Option(None)):
    """Run a callable (module:function) under capture without editing its code."""
    mod_name, _, fn_name = target.partition(":")
    if not mod_name or not fn_name:
        console.print("[red]Target must be module:function[/] — e.g. mypkg.experiments:run_bell")
        raise typer.Exit(1)
    try:
        fn = getattr(importlib.import_module(mod_name), fn_name)
    except ModuleNotFoundError as e:
        console.print(f"[red]Could not import module[/] '{mod_name}': {e}")
        raise typer.Exit(1)
    except AttributeError:
        console.print(f"[red]Module '{mod_name}' has no function '{fn_name}'.[/]")
        raise typer.Exit(1)
    ledger = _ledger()
    capture(project=project, agent=CaptureAgent(ledger), shots=shots)(fn)()
    console.print("[green]captured[/]")


@app.command("list")
def list_runs(limit: int = 50, as_json: bool = typer.Option(False, "--json")):
    """List recorded runs."""
    rows = _ledger().list_runs(limit=limit)
    if as_json:
        console.print_json(json.dumps(rows))
        return
    t = Table("id", "project", "vendor", "backend", "shots", "status", "created")
    for r in rows:
        t.add_row(r["id"][:12], r["project"] or "-", r["vendor"], r["backend"],
                  str(r["shots"]), r["capture_status"], (r["created_at"] or "")[:19])
    console.print(t)


@app.command()
def show(run_id: str, raw: bool = typer.Option(False, "--raw")):
    """Show a run's provenance document."""
    doc = _ledger().get_run_doc(run_id)
    if doc is None:
        console.print("[red]not found[/]")
        raise typer.Exit(1)
    if raw:
        console.print_json(json.dumps(doc, default=str))
        return
    console.print(f"[bold]{doc['run_id']}[/]  run_hash={doc['run_hash'][:16]}…")
    console.print(f"  backend: {doc['backend']['vendor']}/{doc['backend']['name']} ({doc['backend']['kind']})")
    console.print(f"  circuit: {doc['circuit']['format']} n_qubits={doc['circuit']['n_qubits']}")
    console.print(f"  calibration captured_at: {doc['calibration']['captured_at']}")
    console.print(f"  shots: {doc['execution']['shots']}  distribution: {doc['results'][0]['distribution']}")


@app.command()
def reproduce(run_id: str, days: float = 30.0, profile: str = "typical",
              html: str = typer.Option(None, help="write the HTML report to this path")):
    """Re-run a stored result under drifted device state and diff it."""
    report = _ledger().reproduce(run_id, days=days, profile=profile)
    scores = report["scores"]
    console.print(f"[bold]verdict:[/] {report['verdict']}")
    console.print(f"  Hellinger fidelity: {scores['hellinger_fidelity']:.4f}  TVD: {scores['tvd']:.4f}")
    console.print(f"  within shot-noise: {scores['within_shot_noise']}")
    if report.get("calibration_drift"):
        console.print(f"  calibration drift: {len(report['calibration_drift'])} params changed")
    if html:
        from provenova_core.reproduce.report import report_to_html

        with open(html, "w") as f:
            f.write(report_to_html(report))
        console.print(f"  [green]report written[/] → {html}")


@app.command()
def connectors():
    """List discovered connector plugins (and any that couldn't load)."""
    reg = registry()
    for name in reg.available():
        c = reg.get(name)
        console.print(f"  [green]●[/] [bold]{name}[/] → provider={c.provider} v{c.version}")
    for name in reg.unavailable():
        extra = extra_for(name)
        tip = f'pip install "provenova[{extra}]"' if extra else "install its vendor SDK"
        console.print(f"  [yellow]○[/] [bold]{name}[/] — unavailable ([dim]{tip}[/])")
    if not reg.available() and not reg.unavailable():
        console.print("  [dim]no connectors discovered[/]")


@app.command()
def push(dry_run: bool = typer.Option(False, "--dry-run")):
    """Sync local runs to the hosted store."""
    from ..sync import Pusher, SyncClient

    cfg = load_config()
    client = SyncClient(cfg.sync_endpoint, cfg.token)
    result = Pusher(_ledger(), client).push(dry_run=dry_run)
    client.close()
    if result.get("aborted"):
        console.print(f"[red]Push failed:[/] {result['error']}")
        if result.get("hint"):
            console.print(f"  [yellow]→[/] {result['hint']}")
        console.print("  Run [bold]ql doctor[/] to diagnose your connection.")
        raise typer.Exit(1)
    tail = "  [dim](dry run)[/]" if dry_run else ""
    console.print(f"[green]pushed[/] {len(result['pushed'])} · existed {len(result['existed'])} · "
                  f"failed {len(result['failed'])}{tail}")
    for f in result["failed"]:
        console.print(f"  [red]{f['run_id'][:12]}[/]: {f['error']}")
        if f.get("hint"):
            console.print(f"    → {f['hint']}")
    if result["failed"]:
        raise typer.Exit(1)


@app.command()
def login(token: str = typer.Option(..., prompt=True, hide_input=True),
          endpoint: str = typer.Option(None),
          verify: bool = typer.Option(True, help="check the endpoint and token before finishing")):
    """Store an API token for pushing to a hosted store (and verify it)."""
    from ..sync import SyncClient, SyncError

    cfg = load_config()
    cfg.token = token
    if endpoint:
        cfg.sync_endpoint = endpoint
    save_config(cfg)
    console.print(f"[green]token saved[/] for {cfg.sync_endpoint}")
    if verify:
        client = SyncClient(cfg.sync_endpoint, cfg.token)
        try:
            client.health()
            me = client.whoami()
            console.print(f"  [green]verified[/] — signed in as {me.get('email', '?')} "
                          f"(plan: {me.get('plan', '?')})")
        except SyncError as e:
            console.print(f"  [yellow]saved, but verification failed:[/] {e.message}")
            if e.hint:
                console.print(f"    → {e.hint}")
        finally:
            client.close()


@app.command()
def doctor():
    """Diagnose connectivity to the hosted store and local setup."""
    from ..sync import SyncClient, SyncError

    cfg = load_config()
    ok = True
    console.print(f"[bold]Endpoint[/]  {cfg.sync_endpoint}")
    console.print(f"[bold]Token[/]     {'set' if cfg.token else '[yellow]not set — run `ql login`[/]'}")
    console.print(f"[bold]Store[/]     {cfg.store_path}\n")

    client = SyncClient(cfg.sync_endpoint, cfg.token)
    try:
        try:
            h = client.health()
            console.print(f"[green]✓[/] server reachable — Provenova v{h.get('version', '?')} "
                          f"({h.get('deployment', '?')})")
            try:
                from importlib.metadata import version

                cli_v = version("provenova")
                sv = str(h.get("version") or "")
                if sv and cli_v.split(".")[:2] != sv.split(".")[:2]:
                    console.print(f"  [yellow]note[/] client v{cli_v} vs server v{sv} — "
                                  "consider `pip install -U provenova`.")
            except Exception:
                pass
        except SyncError as e:
            ok = False
            console.print(f"[red]✗[/] {e.message}")
            if e.hint:
                console.print(f"    → {e.hint}")

        if cfg.token:
            try:
                me = client.whoami()
                console.print(f"[green]✓[/] token valid — {me.get('email', '?')} "
                              f"(plan: {me.get('plan', '?')})")
            except SyncError as e:
                ok = False
                console.print(f"[red]✗[/] token check failed — {e.message}")
                if e.hint:
                    console.print(f"    → {e.hint}")
    finally:
        client.close()

    reg = registry()
    avail = reg.available()
    console.print(f"\n[bold]Connectors[/] {', '.join(avail) if avail else 'none'}")
    for name in reg.unavailable():
        extra = extra_for(name)
        tip = f'pip install "provenova[{extra}]"' if extra else "install its vendor SDK"
        console.print(f"  [yellow]○[/] {name} unavailable — {tip}")

    console.print("\n[green]All checks passed.[/]" if ok
                  else "\n[red]Problems found — see above.[/]")
    if not ok:
        raise typer.Exit(1)


@app.command()
def config(action: str = typer.Argument("show"), key: str = typer.Argument(None),
           value: str = typer.Argument(None)):
    """get/set/show config (store_path, sync_endpoint, token, default_project)."""
    cfg = load_config()
    if action == "show":
        from dataclasses import asdict

        d = asdict(cfg)
        if d.get("token"):
            d["token"] = "***"
        console.print_json(json.dumps(d))
    elif action == "get":
        console.print(getattr(cfg, key, None))
    elif action == "set":
        setattr(cfg, key, value)
        save_config(cfg)
        console.print("[green]saved[/]")


def main() -> None:
    """Entry point that turns unexpected errors into a one-line message.

    Set QL_DEBUG=1 to see the full traceback. (Normal typer/click exits are
    SystemExit, which is not an Exception and so passes through untouched.)
    """
    from ..sync import SyncError

    try:
        app()
    except SyncError as e:
        console.print(f"[red]Error:[/] {e.message}")
        if e.hint:
            console.print(f"  [yellow]→[/] {e.hint}")
        raise SystemExit(1)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Error:[/] {e}")
        if os.environ.get("QL_DEBUG"):
            raise
        console.print("[dim]Set QL_DEBUG=1 for a full traceback.[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
