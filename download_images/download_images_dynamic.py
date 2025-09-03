#!/usr/bin/env python3
"""
download_images_dynamic.py
Renderiza páginas con scroll infinito y descarga todas las imágenes visibles/cargadas por JS.

Uso:
  python download_images/download_images_dynamic.py "https://tor.myl.cl/cartas/leyendas_pb_3.0" -o ../recursos-myl/leyendas-3 -t 12 

Requisitos:
  pip install playwright beautifulsoup4 requests
  python -m playwright install chromium
"""

import argparse
import os
import re
import sys
import time
import threading
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36")
}

IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp|gif|bmp|svg|tiff|ico)(?:\?|#|$)", re.IGNORECASE)
URL_IN_CSS_RE = re.compile(r'url\((?:\'|")?(.*?)(?:\'|")?\)')

MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/tiff": ".tiff",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
    "application/octet-stream": "",
}

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE)
    return name or "file"

def largest_from_srcset(srcset_value: str) -> str:
    candidates = []
    for part in srcset_value.split(","):
        tokens = part.strip().split()
        url = tokens[0]
        width = 0
        if len(tokens) > 1 and tokens[1].endswith("w"):
            try:
                width = int(tokens[1][:-1])
            except ValueError:
                pass
        candidates.append((width, url))
    return max(candidates, key=lambda x: x[0])[1] if candidates else ""

def guess_ext_from_headers(headers) -> str:
    ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
    return MIME_EXT.get(ctype, "")

def normalize_add(urls_set: set, base_url: str, candidate: str):
    if not candidate:
        return
    candidate = candidate.strip()
    if not candidate or candidate.lower().startswith("data:"):
        return
    abs_url = urljoin(base_url, candidate)
    parsed = urlparse(abs_url)
    if parsed.scheme in ("http", "https"):
        urls_set.add(abs_url)

def collect_from_dom(page, base_url: str) -> set:
    urls = set()
    # 1) Todas las <img>
    img_nodes = page.query_selector_all("img")
    for img in img_nodes:
        # currentSrc (lo que realmente está usando el navegador)
        cs = img.get_attribute("currentSrc")  # a veces None
        if cs:
            normalize_add(urls, base_url, cs)
        # src
        src = img.get_attribute("src")
        if src:
            normalize_add(urls, base_url, src)
        # data-src/lazy
        for attr in ("data-src", "data-lazy-src", "data-original"):
            ds = img.get_attribute(attr)
            if ds:
                normalize_add(urls, base_url, ds)
        # srcset (elige la de mayor ancho)
        ss = img.get_attribute("srcset")
        if ss:
            best = largest_from_srcset(ss)
            normalize_add(urls, base_url, best)

    # 2) background-image en CSS
    # Para rendimiento: recoge solo elementos que tengan style o clase; en páginas chicas puedes usar "*"
    all_nodes = page.query_selector_all("*")
    for node in all_nodes:
        try:
            # Usa evaluate para leer getComputedStyle
            bg = page.evaluate("(el) => getComputedStyle(el).backgroundImage", node)
        except Exception:
            bg = None
        if not bg:
            continue
        # Puede contener múltiples url(...)
        for m in URL_IN_CSS_RE.finditer(bg):
            normalize_add(urls, base_url, m.group(1))

    return urls

def auto_scroll(page, max_scrolls=600, sleep_ms=300, stop_when_stable=6, step_px=200):
    """
    Micro-scroll: mueve la ventana en pasos pequeños (step_px).
    Se detiene cuando scrollHeight y el # de imágenes queda estable 'stop_when_stable' veces seguidas,
    o cuando llega a max_scrolls.
    """
    stable = 0
    last_height = page.evaluate("() => document.body.scrollHeight")
    last_img_count = page.evaluate("() => document.images.length")
    last_y = page.evaluate("() => window.scrollY")

    for i in range(max_scrolls):
        # avanza poquito
        page.evaluate(f"(dy) => window.scrollBy(0, dy)", step_px)
        page.wait_for_timeout(sleep_ms)

        new_height = page.evaluate("() => document.body.scrollHeight")
        new_img_count = page.evaluate("() => document.images.length")
        new_y = page.evaluate("() => window.scrollY")

        # si ya estamos al fondo, intenta “sacudir” un poco para disparar lazy-loads
        if new_y == last_y:
            # pequeño vaivén
            page.evaluate("(dy) => window.scrollBy(0, -Math.floor(dy/2))", step_px)
            page.wait_for_timeout(int(sleep_ms * 0.6))
            page.evaluate("(dy) => window.scrollBy(0, dy)", step_px)
            page.wait_for_timeout(int(sleep_ms * 0.6))

            # re-medir
            new_height = page.evaluate("() => document.body.scrollHeight")
            new_img_count = page.evaluate("() => document.images.length")
            new_y = page.evaluate("() => window.scrollY")

        if new_height <= last_height and new_img_count <= last_img_count and new_y <= last_y:
            stable += 1
        else:
            stable = 0

        last_height = new_height
        last_img_count = new_img_count
        last_y = new_y

        if stable >= stop_when_stable:
            break

    # Barrido final arriba/abajo para disparar cosas rezagadas
    page.evaluate("(h) => window.scrollBy(0, -Math.floor(h*0.4))", page.evaluate("() => window.innerHeight"))
    page.wait_for_timeout(max(600, sleep_ms))
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(max(900, sleep_ms))
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass



def download_one(url: str, out_dir: str, session: requests.Session, index: int):
    try:
        r = session.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        base = sanitize_filename(os.path.basename(urlparse(url).path) or f"image_{index}")
        root, ext = os.path.splitext(base)
        if not ext:
            ext = guess_ext_from_headers(r.headers) or ".bin"
            base = root + ext
        final_path = os.path.join(out_dir, base)
        counter = 1
        while os.path.exists(final_path):
            final_path = os.path.join(out_dir, f"{root}_{counter}{ext}")
            counter += 1
        with open(final_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return final_path, True, "OK"
    except Exception as e:
        return url, False, f"ERROR: {e}"

def main():
    parser = argparse.ArgumentParser(description="Renderiza y descarga imágenes (incluye scroll infinito).")
    parser.add_argument("url", help="URL de la página")
    parser.add_argument("-o", "--output", default=None, help="Carpeta de salida (puede ser relativa, p.ej. ../recursos-myl/daana-aniv)")
    parser.add_argument("-t", "--threads", type=int, default=8, help="Descargas simultáneas")
    parser.add_argument("--viewport-w", type=int, default=1366, help="Viewport width")
    parser.add_argument("--viewport-h", type=int, default=2000, help="Viewport height")
    parser.add_argument("--max-scrolls", type=int, default=300, help="Máximo de ciclos de scroll")
    parser.add_argument("--sleep-ms", type=int, default=400, help="Espera entre scrolls (ms)")
    parser.add_argument("--no-headless", action="store_true", help="Mostrar navegador (debug)")
    args = parser.parse_args()

    base_url = args.url
    parsed = urlparse(base_url)
    if not parsed.scheme.startswith("http"):
        print("URL debe empezar con http:// o https://")
        sys.exit(1)

    out_dir = args.output or f"images_{sanitize_filename(parsed.netloc + parsed.path)}"
    os.makedirs(out_dir, exist_ok=True)

    collected_urls = set()
    lock = threading.Lock()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.no_headless)
        context = browser.new_context(user_agent=HEADERS["User-Agent"], viewport={"width": args.viewport_w, "height": args.viewport_h})
        page = context.new_page()

        # Captura de respuestas de red con imágenes
        def on_response(response):
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                url = response.url
                # Si el header dice image/*, o la URL termina como imagen, lo capturamos
                if ("image/" in ctype) or IMAGE_EXT_RE.search(url):
                    with lock:
                        collected_urls.add(url)
            except Exception:
                pass

        page.on("response", on_response)

        # Cargar página
        page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

        # Scroll progresivo para forzar carga perezosa
        auto_scroll(page, max_scrolls=args.max_scrolls, sleep_ms=args.sleep_ms)

        # Espera breve por últimas cargas en cola
        page.wait_for_load_state("networkidle", timeout=15000)

        # Extraer del DOM renderizado (img/srcset/data-src y background-image)
        dom_urls = collect_from_dom(page, base_url)
        with lock:
            collected_urls.update(dom_urls)

        # Cerrar
        context.close()
        browser.close()

    # Depuración: también intenta raspar el HTML final (por si la página dejó contenido renderizado)
    # (No imprescindible, pero puede sumar alguna URL adicional.)
    try:
        html = requests.get(base_url, headers=HEADERS, timeout=30).text
        soup = BeautifulSoup(html, "html.parser")
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                v = img.get(attr)
                if v:
                    collected_urls.add(urljoin(base_url, v))
            ss = img.get("srcset")
            if ss:
                best = largest_from_srcset(ss)
                if best:
                    collected_urls.add(urljoin(base_url, best))
    except Exception:
        pass

    # Filtrado ligero: solo http(s)
    collected_urls = {u for u in collected_urls if urlparse(u).scheme in ("http", "https")}

    if not collected_urls:
        print("No se encontraron imágenes (posible contenido protegido o renderizado vía canvas).")
        return

    print(f"Encontradas {len(collected_urls)} imágenes únicas.")
    for i, u in enumerate(sorted(collected_urls), 1):
        print(f"  {i:02d}. {u}")

    print(f"Descargando en: {out_dir} con {args.threads} hilos...")
    start = time.time()
    successes = failures = 0
    with requests.Session() as session:
        session.headers.update(HEADERS)
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(download_one, u, out_dir, session, i): u for i, u in enumerate(sorted(collected_urls), 1)}
            for fut in as_completed(futures):
                _, ok, msg = fut.result()
                if ok: successes += 1
                else: failures += 1
                print(f"[{'OK' if ok else '!!'}] {msg}")

    print(f"Listo. Éxitos: {successes}, Fallos: {failures}, Tiempo: {time.time()-start:.1f}s")
    print(f"Carpeta de salida: {out_dir}")

if __name__ == "__main__":
    main()
