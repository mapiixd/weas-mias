#!/usr/bin/env python3
"""
download_images.py
Descarga todas las imágenes encontradas en una página web específica.

Uso:
    python download_images.py https://blog.myl.cl/hijos-de-daana-aniversario -o salida/
Ejemplo:
    python download_images.py "https://blog.myl.cl/hijos-de-daana-aniversario" -o imagenes_myl -t 12
Requisitos:
    pip install requests beautifulsoup4
"""
import argparse
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
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

def find_image_urls(base_url: str) -> list[str]:
    resp = requests.get(base_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    urls = set()
    for img in soup.find_all("img"):
        candidates = []
        src = img.get("src")
        srcset = img.get("srcset")
        data_src = img.get("data-src") or img.get("data-lazy-src") or img.get("data-original")

        if src and not src.lower().startswith("data:"):
            candidates.append(src)
        if srcset:
            best = largest_from_srcset(srcset)
            if best:
                candidates.append(best)
        if data_src and not data_src.lower().startswith("data:"):
            candidates.append(data_src)

        for c in candidates:
            abs_url = urljoin(base_url, c)
            if urlparse(abs_url).scheme in ("http", "https"):
                urls.add(abs_url)

    for meta in ["og:image", "twitter:image"]:
        tag = soup.find("meta", property=meta) or soup.find("meta", attrs={"name": meta})
        if tag and tag.get("content"):
            urls.add(urljoin(base_url, tag["content"]))

    return sorted(urls)

def guess_ext_from_headers(headers) -> str:
    ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
    return MIME_EXT.get(ctype, "")

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
    parser = argparse.ArgumentParser(description="Descarga todas las imágenes de una página web.")
    parser.add_argument("url", help="URL de la página")
    parser.add_argument("-o", "--output", default=None, help="Carpeta de salida")
    parser.add_argument("-t", "--threads", type=int, default=8, help="Hilos simultáneos")
    args = parser.parse_args()

    base_url = args.url
    parsed = urlparse(base_url)
    if not parsed.scheme.startswith("http"):
        print("URL debe empezar con http:// o https://")
        sys.exit(1)

    out_dir = args.output or f"images_{sanitize_filename(parsed.netloc + parsed.path)}"
    os.makedirs(out_dir, exist_ok=True)

    print(f"Buscando imágenes en: {base_url}")
    img_urls = find_image_urls(base_url)
    if not img_urls:
        print("No se encontraron imágenes")
        return

    print(f"Descargando {len(img_urls)} imágenes en {out_dir}...")
    start = time.time()
    successes = failures = 0
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(download_one, u, out_dir, session, i): u for i, u in enumerate(img_urls, 1)}
            for fut in as_completed(futures):
                _, ok, msg = fut.result()
                if ok: successes += 1
                else: failures += 1
                print(f"[{'OK' if ok else '!!'}] {msg}")
    print(f"Listo. Éxitos: {successes}, Fallos: {failures}, Tiempo: {time.time()-start:.1f}s")

if __name__ == "__main__":
    main()
