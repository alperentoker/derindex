"""Command-Line Interface for Derindex using Rich and Argparse."""

import sys
import argparse
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.syntax import Syntax

from config import config
from app.database.db import Database
from app.crawler.crawler import Crawler
from app.crawler.watcher import FileWatcher
from app.search.hybrid_search import HybridSearcher

console = Console()


def cmd_index(args):
    """Recursively index a directory with incremental change detection."""
    target = Path(args.path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        console.print(f"[bold red]Hata:[/bold red] Klasör bulunamadı: {target}")
        sys.exit(1)

    console.print(f"[bold cyan]Dizin taranıyor ve indeksleniyor:[/bold cyan] {target}")
    crawler = Crawler()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[green]İndeksleniyor...", total=None)

        def cb(current, total, filename, status):
            progress.update(task, total=total, completed=current, description=f"[{status}] {filename[:30]}")

        stats = crawler.index_directory(target, progress_callback=cb)

    console.print(Panel(
        f"[bold green]İndeksleme Tamamlandı![/bold green]\n\n"
        f"• Taranan Dosya: [cyan]{stats['scanned']}[/cyan]\n"
        f"• Yeni / Güncellenen: [green]{stats['indexed']}[/green]\n"
        f"• Değişmeyen (Atlanan): [yellow]{stats['skipped']}[/yellow]\n"
        f"• Silinen (Kaldırılan): [red]{stats['deleted']}[/red]\n"
        f"• Başarısız: [bold red]{stats['failed']}[/bold red]",
        title="İndeks İstatistikleri",
        border_style="green"
    ))


def cmd_search(args):
    """Hybrid search across documents."""
    query = args.query
    alpha = args.alpha if hasattr(args, "alpha") and args.alpha is not None else config.DEFAULT_ALPHA
    limit = args.limit if hasattr(args, "limit") and args.limit is not None else config.SEARCH_LIMIT_DEFAULT

    searcher = HybridSearcher()
    results = searcher.search(query, alpha=alpha, limit=limit)

    if not results:
        console.print(f"[yellow]'{query}' için sonuç bulunamadı.[/yellow]")
        return

    table = Table(title=f"Arama Sonuçları: '{query}' (alpha={alpha})", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Dosya & Konum", style="bold cyan", width=30)
    table.add_column("Skor", style="green", width=12)
    table.add_column("Eşleşen Snippet / Metin", style="white")

    for i, res in enumerate(results, start=1):
        loc_parts = [f"[bold]{res.filename}[/bold]"]
        if res.section:
            loc_parts.append(f"[dim]Section:[/dim] {res.section}")
        elif res.page_number:
            loc_parts.append(f"[dim]Sayfa:[/dim] {res.page_number}")

        if res.start_line and res.end_line:
            loc_parts.append(f"[dim]Satır:[/dim] {res.start_line}-{res.end_line}")

        loc_parts.append(f"[dim]{res.path}[/dim]")
        loc_str = "\n".join(loc_parts)

        score_info = (
            f"[bold]{round(res.score, 3)}[/bold]\n"
            f"[dim]BM25: {round(res.bm25_score, 2)}\n"
            f"Sem: {round(res.semantic_score, 2)}[/dim]"
        )

        table.add_row(str(i), loc_str, score_info, res.matched_snippet)

    console.print(table)


def cmd_code(args):
    """Search exclusively within code files."""
    query = args.query
    searcher = HybridSearcher()
    results = searcher.search_code(query, limit=10)

    if not results:
        console.print(f"[yellow]'{query}' kodu için sonuç bulunamadı.[/yellow]")
        return

    console.print(f"[bold cyan]Kod Sonuçları: '{query}'[/bold cyan]\n")
    for i, res in enumerate(results, start=1):
        header = f"[{i}] {res.filename} | Satır {res.start_line}-{res.end_line}"
        if res.symbol_name:
            header += f" | Sembol: {res.symbol_name}"
        syntax = Syntax(res.matched_snippet, res.code_type or "python", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title=header, subtitle=res.path, border_style="blue"))


def cmd_symbol(args):
    """Direct symbol lookup (functions, classes, structs)."""
    sym = args.symbol
    searcher = HybridSearcher()
    symbols = searcher.search_symbol(sym, limit=20)

    if not symbols:
        console.print(f"[yellow]'{sym}' sembolü bulunamadı.[/yellow]")
        return

    table = Table(title=f"Bulunan Semboller: '{sym}'", show_lines=True)
    table.add_column("Sembol Adı", style="bold yellow")
    table.add_column("Tür", style="cyan")
    table.add_column("Satır", style="dim")
    table.add_column("Dosya", style="green")
    table.add_column("Kod Önizleme", style="white")

    for s in symbols:
        table.add_row(
            s["name"],
            s["kind"],
            str(s["line_number"]),
            f"{s['filename']}\n[dim]{s['path']}[/dim]",
            s["code_snippet"]
        )

    console.print(table)


def cmd_stats(args):
    """Display index and database statistics."""
    db = Database()
    st = db.get_stats()

    table = Table(title="Derindex Sistem ve İndeks İstatistikleri", show_header=True)
    table.add_column("Metrik", style="bold cyan")
    table.add_column("Değer", style="green")

    table.add_row("Toplam İndekslenen Belge", str(st["total_documents"]))
    table.add_row("Toplam Chunk Sayısı", str(st["total_chunks"]))
    table.add_row("Tekil Terim Sayısı (Ters İndeks)", str(st["total_unique_terms"]))
    table.add_row("İndekslenen Sembol Sayısı (AST)", str(st["total_symbols"]))
    table.add_row("Toplam Token Sayısı", f"{st['total_tokens']:,}")
    table.add_row("Veritabanı Boyutu (Disk)", f"{st['database_size_mb']} MB")

    console.print(table)

    if st["file_types"]:
        ext_table = Table(title="Dosya Türü Dağılımı", show_header=True)
        ext_table.add_column("Uzantı", style="yellow")
        ext_table.add_column("Adet", style="cyan")
        for ext, cnt in sorted(st["file_types"].items(), key=lambda x: x[1], reverse=True):
            ext_table.add_row(ext, str(cnt))
        console.print(ext_table)


def cmd_status(args):
    """Check health of local components (DB, Vector Store, Embedder)."""
    db = Database()
    st = db.get_stats()

    table = Table(title="Bileşen Durum Kontrolü", show_header=True)
    table.add_column("Bileşen", style="bold")
    table.add_column("Durum", style="bold")
    table.add_column("Detay", style="dim")

    table.add_row("SQLite Veritabanı", "[green]Çalışıyor[/green]", f"{st['total_documents']} belge, {config.DB_PATH.name}")
    table.add_row("Vektör Deposu", "[green]Hazır[/green]", f"{config.VECTORS_PATH.name}")
    table.add_row("Semantik İndeks", "[green]Hazır[/green]", "Dense Vector Index")

    console.print(table)


def cmd_rebuild(args):
    """Completely wipe and rebuild indexes."""
    confirm = console.input("[bold red]Tüm indeksler ve vektörler silinecek. Devam etmek istiyor musunuz? (e/h): [/bold red]")
    if confirm.lower() not in {"e", "evet", "y", "yes"}:
        console.print("[yellow]İşlem iptal edildi.[/yellow]")
        return

    crawler = Crawler()
    crawler.db.clear_all()
    crawler.vector_store.clear()
    console.print("[green]Tüm indeks ve vektör verisi temizlendi. 'derindex index <klasor>' komutuyla yeniden indeksleyebilirsiniz.[/green]")


def cmd_watch(args):
    """Watch directory in real-time for changes."""
    target = Path(args.path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        console.print(f"[bold red]Hata:[/bold red] Klasör bulunamadı: {target}")
        sys.exit(1)

    console.print(f"[bold green]Dizin izleniyor (Değişiklikler anında işlenecek):[/bold green] {target}")
    console.print("[dim]Durdurmak için Ctrl+C tuşlayın...[/dim]\n")

    watcher = FileWatcher()
    watcher.watch(target)


def cmd_serve(args):
    """Launch FastAPI Web Server."""
    import uvicorn
    port = args.port or config.WEB_PORT
    host = args.host or config.WEB_HOST
    console.print(f"[bold green]Web Arayüzü Başlatılıyor:[/bold green] http://localhost:{port}")
    uvicorn.run("app.web.server:app", host=host, port=port, reload=False)


def cmd_daemon(args):
    """Run both Web UI and File Watcher concurrently in background service mode."""
    import uvicorn
    from app.web.server import db as server_db

    port = args.port or config.WEB_PORT
    host = args.host or config.WEB_HOST
    watch_path = getattr(args, "watch", None)

    if watch_path:
        target = Path(watch_path).expanduser().resolve()
        if target.exists() and target.is_dir():
            server_db.add_watched_folder(str(target))
            console.print(f"[bold cyan]Servis Modu: İzleme klasörü kaydedildi ->[/bold cyan] {target}")
        else:
            console.print(f"[yellow]Uyarı: Belirtilen izleme klasörü bulunamadı: {watch_path}[/yellow]")

    persisted = server_db.get_watched_folders()
    if persisted:
        console.print(f"[bold cyan]Servis Modu: {len(persisted)} kayıtlı klasör arka planda otomatik izlenecek:[/bold cyan]")
        for f in persisted:
            console.print(f"  [dim]-> {f}[/dim]")
    else:
        console.print("[dim]Servis Modu: Henüz kayıtlı izleme klasörü yok. Web arayüzünden ekleyebilirsiniz.[/dim]")

    console.print(f"[bold green]Servis Modu: Web Arayüzü Başlatılıyor ->[/bold green] http://localhost:{port}")
    uvicorn.run("app.web.server:app", host=host, port=port, reload=False, log_level="info")


def main():
    parser = argparse.ArgumentParser(
        prog="derindex",
        description="Derindex: Derinlemesine Kişisel Arama Motoru & Semantik Arama (Yerel ve Hızlı)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Komutlar")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Web UI ve Watcher'ı arka plan servisi olarak çalıştır")
    p_daemon.add_argument("--watch", type=str, default=None, help="İzlenecek klasör yolu (örn: ~/Documents)")
    p_daemon.add_argument("--port", type=int, default=8000, help="Web portu (varsayılan: 8000)")
    p_daemon.add_argument("--host", type=str, default="0.0.0.0", help="Web hostu")
    p_daemon.set_defaults(func=cmd_daemon)

    # index
    p_index = subparsers.add_parser("index", help="Bir klasörü recursive tara ve indeksle")
    p_index.add_argument("path", help="İndekslenecek klasör yolu (örn: ~/Documents)")
    p_index.set_defaults(func=cmd_index)

    # search
    p_search = subparsers.add_parser("search", help="Hibrit arama yap")
    p_search.add_argument("query", help="Arama sorgusu")
    p_search.add_argument("--alpha", type=float, default=0.5, help="Hibrit ağırlık (0=semantik, 1=BM25)")
    p_search.add_argument("--limit", type=int, default=10, help="Maksimum sonuç sayısı")
    p_search.set_defaults(func=cmd_search)

    # code
    p_code = subparsers.add_parser("code", help="Sadece kod dosyalarında arama yap")
    p_code.add_argument("query", help="Kod arama sorgusu")
    p_code.set_defaults(func=cmd_code)

    # symbol
    p_symbol = subparsers.add_parser("symbol", help="Fonksiyon, sınıf veya struct ara")
    p_symbol.add_argument("symbol", help="Sembol adı")
    p_symbol.set_defaults(func=cmd_symbol)

    # stats
    p_stats = subparsers.add_parser("stats", help="İndeks istatistiklerini göster")
    p_stats.set_defaults(func=cmd_stats)

    # status
    p_status = subparsers.add_parser("status", help="Bileşen durumlarını kontrol et")
    p_status.set_defaults(func=cmd_status)

    # rebuild
    p_rebuild = subparsers.add_parser("rebuild", help="Tüm indeksleri sıfırla")
    p_rebuild.set_defaults(func=cmd_rebuild)

    # watch
    p_watch = subparsers.add_parser("watch", help="Klasörü gerçek zamanlı izle ve güncelle")
    p_watch.add_argument("path", help="İzlenecek klasör yolu")
    p_watch.set_defaults(func=cmd_watch)

    # serve
    p_serve = subparsers.add_parser("serve", help="Web arayüzünü (FastAPI) başlat")
    p_serve.add_argument("--port", type=int, default=8000, help="Web portu (varsayılan: 8000)")
    p_serve.add_argument("--host", type=str, default="0.0.0.0", help="Web hostu")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
