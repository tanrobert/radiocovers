"""
fetch_covers.py — scarica le copertine album via iTunes Search API.

iTunes: gratuita, nessuna API key, ~20 req/s sostenibili.

Strategia: 1 chiamata per BRANO (artista + titolo) → iTunes restituisce
il metadato del brano incluso nome album e copertina.
Cache key = "artista|titolo" per ogni brano unico.
La deduplicazione per album avviene in Astro a build time.

Prima run: ~11.400 brani × 0.14s ≈ 25-30 minuti.
Run successive: solo i nuovi brani (tipicamente 0-10/ora).

covers.json struttura:
  {
    "Genesis|Supper's Ready": {
      "coverUrl": "https://is1-ssl.mzstatic.com/.../600x600bb.jpg",
      "albumName": "Foxtrot",
      "artistName": "Genesis"
    },
    ...
  }
"""

import json
import re
import time
import sys
import requests
from pathlib import Path

PLAYLIST_PATH = Path("playlist.txt")
CACHE_PATH    = Path("src/data/covers.json")
ITUNES_URL    = "https://itunes.apple.com/search"
DELAY         = 0.14   # secondi tra richieste (~7 req/s, abbondantemente sotto il limite)


def parse_line(line: str) -> tuple[str, str] | None:
    """Estrae (artista, titolo) da una riga del file playlist.

    Formati supportati:
      - "Artista: Titolo"
      - "Artista / Titolo"
      - "Artista - Titolo"
      - "Artista   Titolo"  (3+ spazi)
    """
    line = line.strip()
    if not line:
        return None

    for sep in (": ", " / ", " - "):
        if sep in line:
            artist, _, title = line.partition(sep)
            artist = artist.strip()
            title  = title.strip()
            if len(artist) >= 2 and len(title) >= 1:
                return (artist, title)
            return None

    m = re.search(r" {3,}", line)
    if m:
        artist = line[:m.start()].strip()
        title  = line[m.end():].strip()
        if len(artist) >= 2 and len(title) >= 1:
            return (artist, title)

    return None


def fetch_track(artist: str, title: str) -> dict:
    """Cerca un brano su iTunes e restituisce { coverUrl, albumName, artistName }.

    Ritorna valori None se non trovato.
    """
    try:
        r = requests.get(
            ITUNES_URL,
            params={
                "term":    f"{artist} {title}",
                "media":   "music",
                "entity":  "song",
                "limit":   5,
            },
            timeout=12,
        )
        r.raise_for_status()
        results = r.json().get("results", [])

        # Cerca il risultato con artista più simile
        artist_lower = artist.lower()
        for result in results:
            if artist_lower in result.get("artistName", "").lower():
                cover = result.get("artworkUrl100", "")
                # Scala a 600x600 (iTunes supporta fino a 3000x3000)
                cover = cover.replace("100x100bb", "600x600bb") if cover else None
                return {
                    "coverUrl":   cover or None,
                    "albumName":  result.get("collectionName") or None,
                    "artistName": result.get("artistName") or artist,
                }

        # Fallback: primo risultato qualsiasi
        if results:
            cover = results[0].get("artworkUrl100", "")
            cover = cover.replace("100x100bb", "600x600bb") if cover else None
            return {
                "coverUrl":   cover or None,
                "albumName":  results[0].get("collectionName") or None,
                "artistName": results[0].get("artistName") or artist,
            }

    except Exception as e:
        print(f"  Errore fetch '{artist} | {title}': {e}", file=sys.stderr)

    return {"coverUrl": None, "albumName": None, "artistName": artist}


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

    # Estrai brani unici dalla playlist
    lines = PLAYLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    tracks: list[tuple[str, str]] = []
    seen: set[str] = set()
    skipped = 0

    for line in lines:
        parsed = parse_line(line)
        if not parsed:
            skipped += 1
            continue
        artist, title = parsed
        key = f"{artist}|{title}"
        if key not in seen:
            seen.add(key)
            tracks.append((artist, title))

    # Solo i brani non ancora in cache (con dati validi o con None esplicito)
    new_tracks = [(a, t) for (a, t) in tracks if f"{a}|{t}" not in cache]

    print(f"Brani unici: {len(tracks)}  |  Senza artista/titolo: {skipped}")
    print(f"In cache: {len(cache)}  |  Da fetchare: {len(new_tracks)}")
    estimated = len(new_tracks) * DELAY / 60
    print(f"Tempo stimato: ~{estimated:.1f} min")

    if not new_tracks:
        print("Nessun nuovo brano. Cache aggiornata.")
        return

    fetched = missed = total = 0

    for i, (artist, title) in enumerate(new_tracks, 1):
        key  = f"{artist}|{title}"
        data = fetch_track(artist, title)
        cache[key] = data
        total += 1

        if data["coverUrl"]:
            fetched += 1
            print(f"[{i}/{len(new_tracks)}] OK  {artist} — {data['albumName']}")
        else:
            missed += 1
            print(f"[{i}/{len(new_tracks)}] --  {artist} | {title}")

        # Checkpoint ogni 100 brani
        if i % 100 == 0:
            CACHE_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  (checkpoint: {len(cache)} brani, {fetched} con cover)")

        time.sleep(DELAY)

    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDone — trovati: {fetched}, non trovati: {missed}, totale: {total}")


if __name__ == "__main__":
    main()
