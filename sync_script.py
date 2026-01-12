import os
import sys
import json
import requests
import yt_dlp
import subprocess
from pathlib import Path
import time
import shutil

# Konfiguration aus Umgebungsvariablen
PLAYLIST_ID = os.getenv('YOUTUBE_PLAYLIST_ID', '').strip()
PCLOUD_USER = os.getenv('PCLOUD_USERNAME', '').strip()
PCLOUD_PASS = os.getenv('PCLOUD_PASSWORD', '').strip()
PCLOUD_FOLDER = os.getenv('PCLOUD_FOLDER', '/YouTube').strip()
PCLOUD_REGION = os.getenv('PCLOUD_REGION', 'EU').strip()
YOUTUBE_COOKIES = os.getenv('YOUTUBE_COOKIES', '').strip()
DOWNLOADED_FILE = 'downloaded_videos.txt'
COOKIES_FILE = 'cookies.txt'

# API URLs basierend auf Region
PCLOUD_API_URL = 'https://eapi.pcloud.com' if PCLOUD_REGION == 'EU' else 'https://api.pcloud.com'

def check_ffmpeg():
    """Prüfe ob ffmpeg verfügbar ist"""
    if not shutil.which('ffmpeg'):
        print("❌ FEHLER: ffmpeg ist nicht installiert!")
        print("   Installiere es mit: sudo apt-get install ffmpeg")
        return False
    print("✓ ffmpeg gefunden")
    return True

def load_downloaded_videos():
    """Lade Liste der bereits heruntergeladenen Videos"""
    if os.path.exists(DOWNLOADED_FILE):
        with open(DOWNLOADED_FILE, 'r') as f:
            return set(line.strip() for line in f)
    return set()

def save_downloaded_video(video_id):
    """Speichere Video ID als heruntergeladen"""
    with open(DOWNLOADED_FILE, 'a') as f:
        f.write(f"{video_id}\n")

def setup_cookies():
    """Erstelle Cookies-Datei aus Umgebungsvariable"""
    if YOUTUBE_COOKIES:
        with open(COOKIES_FILE, 'w') as f:
            f.write(YOUTUBE_COOKIES)
        print("✓ YouTube Cookies geladen")
        return True
    else:
        print("⚠️  Keine YouTube Cookies gefunden")
        return False

def get_playlist_videos():
    """Hole alle Video IDs aus der Playlist"""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': False,
        'no_warnings': True
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    playlist_url = f'https://www.youtube.com/playlist?list={PLAYLIST_ID}'
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
            if 'entries' in info:
                return [(entry['id'], entry['title']) for entry in info['entries'] if entry]
    except Exception as e:
        print(f"❌ Fehler beim Abrufen der Playlist: {e}")
        return []
    
    return []

def download_video(video_id, max_retries=3):
    """Lade Video als m4a herunter mit Retry-Mechanismus"""
    
    for attempt in range(max_retries):
        try:
            print(f"  Versuch {attempt + 1}/{max_retries}...")
            
            # Optimierte yt-dlp Optionen
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': 'downloads/%(id)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a',
                    'preferredquality': '192',
                }],
                'quiet': False,
                'no_warnings': False,
                'ignoreerrors': False,
                # Erhöhe Timeout
                'socket_timeout': 30,
                # Retry bei Netzwerkfehlern
                'retries': 3,
                'fragment_retries': 3,
            }
            
            # Cookies hinzufügen falls vorhanden
            if os.path.exists(COOKIES_FILE):
                ydl_opts['cookiefile'] = COOKIES_FILE
            
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                
                # Finde die heruntergeladene Datei
                download_dir = Path('downloads')
                m4a_file = download_dir / f'{video_id}.m4a'
                
                if m4a_file.exists():
                    print(f"  ✓ Download erfolgreich: {m4a_file.name}")
                    return str(m4a_file)
                
                # Falls m4a nicht existiert, suche nach anderen Formaten
                possible_files = list(download_dir.glob(f'{video_id}.*'))
                if not possible_files:
                    raise Exception("Keine Datei nach Download gefunden")
                
                downloaded_file = possible_files[0]
                
                # Konvertiere zu m4a falls nötig
                if downloaded_file.suffix != '.m4a':
                    print(f"  Konvertiere {downloaded_file.suffix} zu m4a...")
                    
                    result = subprocess.run([
                        'ffmpeg', '-i', str(downloaded_file),
                        '-c:a', 'aac', '-b:a', '192k',
                        '-vn',
                        '-y',
                        str(m4a_file)
                    ], capture_output=True, text=True, timeout=300)
                    
                    if result.returncode != 0:
                        raise Exception(f"FFmpeg Fehler: {result.stderr}")
                    
                    # Lösche Original
                    downloaded_file.unlink()
                    downloaded_file = m4a_file
                
                print(f"  ✓ Download erfolgreich: {downloaded_file.name}")
                return str(downloaded_file)
                
        except Exception as e:
            print(f"  ✗ Versuch {attempt + 1} fehlgeschlagen: {e}")
            
            # Cleanup bei Fehler
            download_dir = Path('downloads')
            for f in download_dir.glob(f'{video_id}.*'):
                try:
                    f.unlink()
                except:
                    pass
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                print(f"  Warte {wait_time} Sekunden vor erneutem Versuch...")
                time.sleep(wait_time)
            else:
                raise Exception(f"Download nach {max_retries} Versuchen fehlgeschlagen")

def pcloud_auth():
    """Authentifiziere bei PCloud"""
    url = f'{PCLOUD_API_URL}/userinfo'
    
    params = {
        'username': PCLOUD_USER.strip(),
        'password': PCLOUD_PASS.strip(),
        'getauth': 1
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        data = response.json()
        
        if data.get('result') == 0:
            print(f"  ✓ PCloud Authentifizierung erfolgreich")
            return data['auth']
        else:
            error_msg = data.get('error', 'Unbekannter Fehler')
            error_code = data.get('result', 'Unbekannt')
            raise Exception(f"PCloud Auth failed - Code: {error_code}, Error: {error_msg}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Netzwerk-Fehler bei PCloud Auth: {e}")

def pcloud_create_folder(auth, folder_path):
    """Erstelle Ordner in PCloud falls nicht vorhanden"""
    url = f'{PCLOUD_API_URL}/createfolderifnotexists'
    params = {
        'auth': auth,
        'path': folder_path
    }
    
    response = requests.get(url, params=params, timeout=30)
    result = response.json()
    
    if result.get('result') == 0:
        print(f"  ✓ Ordner bereit: {folder_path}")
    
    return result

def pcloud_upload(auth, local_file, remote_path):
    """Lade Datei zu PCloud hoch"""
    url = f'{PCLOUD_API_URL}/uploadfile'
    
    params = {
        'auth': auth,
        'path': remote_path,
        'filename': os.path.basename(local_file)
    }
    
    with open(local_file, 'rb') as f:
        files = {'file': f}
        response = requests.post(url, params=params, files=files, timeout=300)
    
    return response.json()

def main():
    print("🚀 Starte YouTube to PCloud Sync...")
    
    # Prüfe ob ffmpeg vorhanden ist
    if not check_ffmpeg():
        sys.exit(1)
    
    # Validiere Umgebungsvariablen
    if not all([PLAYLIST_ID, PCLOUD_USER, PCLOUD_PASS]):
        print("❌ Fehlende Umgebungsvariablen!")
        print(f"   PLAYLIST_ID: {'✓' if PLAYLIST_ID else '✗'}")
        print(f"   PCLOUD_USERNAME: {'✓' if PCLOUD_USER else '✗'}")
        print(f"   PCLOUD_PASSWORD: {'✓' if PCLOUD_PASS else '✗'}")
        sys.exit(1)
    
    # Setup Cookies
    setup_cookies()
    
    # Lade bereits heruntergeladene Videos
    downloaded = load_downloaded_videos()
    print(f"📋 {len(downloaded)} Videos bereits heruntergeladen")
    
    # Hole Playlist Videos
    print(f"🔍 Suche neue Videos in Playlist {PLAYLIST_ID}...")
    playlist_videos = get_playlist_videos()
    
    if not playlist_videos:
        print("❌ Keine Videos in Playlist gefunden!")
        sys.exit(1)
    
    print(f"📺 {len(playlist_videos)} Videos in Playlist gefunden")
    
    # Finde neue Videos
    new_videos = [(vid, title) for vid, title in playlist_videos if vid not in downloaded]
    
    if not new_videos:
        print("✅ Keine neuen Videos gefunden!")
        return
    
    print(f"🆕 {len(new_videos)} neue Videos gefunden!")
    
    # Authentifiziere bei PCloud
    print("🔐 Authentifiziere bei PCloud...")
    auth = pcloud_auth()
    
    # Erstelle Zielordner
    pcloud_create_folder(auth, PCLOUD_FOLDER)
    
    # Statistiken
    success_count = 0
    failed_videos = []
    
    # Verarbeite neue Videos
    for i, (video_id, title) in enumerate(new_videos, 1):
        try:
            print(f"\n📥 [{i}/{len(new_videos)}] Lade herunter: {title}")
            local_file = download_video(video_id)
            
            print(f"☁️  Lade hoch zu PCloud...")
            result = pcloud_upload(auth, local_file, PCLOUD_FOLDER)
            
            if result.get('result') == 0:
                print(f"✅ Erfolgreich: {title}")
                save_downloaded_video(video_id)
                success_count += 1
                
                # Lösche lokale Datei
                try:
                    os.remove(local_file)
                except:
                    pass
            else:
                print(f"❌ Upload fehlgeschlagen: {result}")
                failed_videos.append((title, "Upload fehlgeschlagen"))
                
        except Exception as e:
            print(f"❌ Fehler bei {title}: {e}")
            failed_videos.append((title, str(e)))
            continue
    
    # Zusammenfassung
    print(f"\n{'='*60}")
    print(f"🎉 Sync abgeschlossen!")
    print(f"   ✅ Erfolgreich: {success_count}/{len(new_videos)}")
    print(f"   ❌ Fehlgeschlagen: {len(failed_videos)}/{len(new_videos)}")
    
    if failed_videos:
        print(f"\n⚠️  Fehlgeschlagene Videos:")
        for title, error in failed_videos:
            print(f"   - {title}: {error}")
    
    print(f"{'='*60}")

if __name__ == '__main__':
    main()