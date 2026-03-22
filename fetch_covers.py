"""
fetch_covers.py — scarica le copertine album via Deezer API.

Deezer: gratuita, nessuna API key, rate limit generoso (~50 req/s).

Strategia: 1 chiamata per ARTISTA → Deezer restituisce tutti i suoi album
con le copertine in una sola risposta.
~1.400 chiamate totali, prima run in ~12 minuti a 0.5s/req.

Cache key = nome artista.
covers.json struttura:
  { "Genesis": [ {albumName, coverUrl}, ... ], ... }
"""

import json
import re
import time
import sys
import requests
from pathlib import Path

PLAYLIST_PATH = Path("playlist.txt")
CACHE_PATH    = Path("src/data/covers.json")
DEEZER_URL    = "https://api.deezer.com"
DELAY         = 0.5    # secondi tra richieste


def parse_line(line: str) -> str | None:
    """Estrae il nome artista da una riga del file playlist."""
    line = line.strip()
    if not line:
        return None

    for sep in (": ", " / ", " - "):
        if sep in line:
            artist = line.partition(sep)[0].strip()
            return artist if len(artist) >= 2 else None

    m = re.search(r" {3,}", line)
    if m:
        artist = line[:m.start()].strip()
        return artist if len(artist) >= 2 else None

    return None


def fetch_albums(artist: str) -> list[dict]:
    """Cerca tutti gli album di un artista su Deezer.

    Flusso:
      1. Cerca l'artista per nome → prendi il primo risultato
      2. Recupera i suoi album → filtra quelli con copertina
    Ritorna lista di {albumName, coverUrl}.
    """
    try:
        r = requests.get(
            f"{DEEZER_URL}/search/artist",
            params={"q": artist, "limit": 1},
            timeout=12,
        )
        r.raise_for_status()
        artists_data = r.json().get("data", [])
        if not artists_data:
            return []

        artist_id = artists_data[0]["id"]
        time.sleep(0.2)

        r2 = requests.get(
            f"{DEEZER_URL}/artist/{artist_id}/albums",
            params={"limit": 100},
            timeout=12,
        )
        r2.raise_for_status()
        albums_data = r2.json().get("data", [])

        albums = []
        seen   = set()
        for album in albums_data:
            name      = album.get("title", "").strip()
            cover_url = album.get("cover_xl") or album.get("cover_big") or album.get("cover", "")
            if not name or not cover_url or name in seen:
                continue
            record_type = album.get("record_type", "")
            if record_type in ("single", "ep"):
                continue
            seen.add(name)
            albums.append({"albumName": name, "coverUrl": cover_url})

        return albums

    except Exception as e:
        print(f"  Errore fetch '{artist}': {e}", file=sys.stderr)
        return []


def main() -> None:
    if not PLAYLIST_PATH.exists():
        print(f"ERRORE: {PLAYLIST_PATH} non trovato.", file=sys.stderr)
        sys.exit(1)

    # Carica cache esistente
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    if CACHE_PATH.exists() and CACHE_PATH.stat().st_size > 2:
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("AVVISO: covers.json corrotto, parto da zero.", file=sys.stderr)

    # Estrai artisti unici dalla playlist
    lines = PLAYLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    artists: list[str] = []
    seen_artists: set[str] = set()
    skipped = 0

    for line in lines:
        artist = parse_line(line)
        if not artist:
            skipped += 1
            continue
        if artist not in seen_artists:
            seen_artists.add(artist)
            artists.append(artist)

    # Salta solo artisti che hanno già album reali in cache
    # (ri-fetcha artisti con array vuoto [])
    new_artists = [a for a in artists if a not in cache or not cache[a]]

    print(f"Artisti unici: {len(artists)}  |  Senza artista: {skipped}")
    print(f"In cache: {len(cache)}  |  Da fetchare: {len(new_artists)}")
    print(f"Tempo stimato: ~{len(new_artists) * DELAY * 2 / 60:.1f} min")

    if not new_artists:
        print("Nessun nuovo artista. Cache aggiornata.")
        return

    fetched = missed = total_albums = 0

    for i, artist in enumerate(new_artists, 1):
        albums = fetch_albums(artist)
        cache[artist] = albums

        if albums:
            fetched += 1
            total_albums += len(albums)
            print(f"[{i}/{len(new_artists)}] OK ({len(albums)} album) {artist}")
        else:
            missed += 1
            print(f"[{i}/{len(new_artists)}] -- {artist}")

        if i % 50 == 0:
            CACHE_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  (checkpoint: {len(cache)} artisti, {total_albums} album)")

        time.sleep(DELAY)

    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDone -- artisti trovati: {fetched}, non trovati: {missed}")
    print(f"Album totali in covers.json: {total_albums}")


if __name__ == "__main__":
    main()
