import os
import sys
import json
import requests
import yt_dlp
import subprocess
from pathlib import Path
import time
import shutil
import re

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

def sanitize_filename(filename):
    """Bereinige Dateinamen für sicheres Speichern"""
    # Entferne oder ersetze problematische Zeichen
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'\s+', ' ', filename)
    return filename.strip()

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

def download_video(video_id, video_title, max_retries=3):
    """Lade Video als m4a herunter mit Retry-Mechanismus"""
    
    # Bereinige Titel für finalen Dateinamen
    safe_title = sanitize_filename(video_title)
    
    for attempt in range(max_retries):
        try:
            print(f"  Versuch {attempt + 1}/{max_retries}...")
            
            # Cleanup vorheriger Versuche
            download_dir = Path('downloads')
            for f in download_dir.glob(f'{video_id}*'):
                try:
                    f.unlink()
                    print(f"  Gelöscht: {f.name}")
                except:
                    pass
            
            # Optimierte yt-dlp Optionen - temporär mit video_id
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': f'downloads/{video_id}.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a',
                    'preferredquality': '192',
                }],
                'keepvideo': False,
                'quiet': False,
                'no_warnings': False,
                'ignoreerrors': False,
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'extractor_retries': 3,
            }
            
            # Cookies hinzufügen falls vorhanden
            if os.path.exists(COOKIES_FILE):
                ydl_opts['cookiefile'] = COOKIES_FILE
            
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
            
            # Warte kurz damit Dateisystem Zeit hat
            time.sleep(2)
            
            # Suche die heruntergeladene Datei (nach video_id)
            possible_files = list(download_dir.glob(f'{video_id}*.m4a'))
            
            if not possible_files:
                # Falls keine m4a, suche andere Formate
                possible_files = list(download_dir.glob(f'{video_id}*'))
                possible_files = [f for f in possible_files if f.suffix in ['.webm', '.mp4', '.m4a', '.opus']]
            
            if not possible_files:
                raise Exception(f"Keine Datei gefunden nach Download. Gesucht: {video_id}*")
            
            downloaded_file = possible_files[0]
            print(f"  ✓ Gefundene Datei: {downloaded_file.name}")
            
            # Ziel m4a Datei MIT TITEL-NAME für PCloud
            final_filename = f'{safe_title}.m4a'
            m4a_file = download_dir / final_filename
            
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
            else:
                # Rename zu Titel-basiertem Namen
                downloaded_file.rename(m4a_file)
            
            print(f"  ✓ Download erfolgreich: {m4a_file.name}")
            print(f"  📁 Finale Datei für Upload: {final_filename}")
            return str(m4a_file)
                
        except Exception as e:
            print(f"  ✗ Versuch {attempt + 1} fehlgeschlagen: {e}")
            
            # Debug: Liste alle Dateien im downloads Ordner
            download_dir = Path('downloads')
            all_files = list(download_dir.glob('*'))
            print(f"  Debug - Dateien im downloads Ordner:")
            for f in all_files:
                print(f"    - {f.name}")
            
            # Cleanup bei Fehler
            for f in download_dir.glob(f'{video_id}*'):
                try:
                    f.unlink()
                except:
                    pass
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 10
                print(f"  Warte {wait_time} Sekunden vor erneutem Versuch...")
                time.sleep(wait_time)
            else:
                raise Exception(f"Download nach {max_retries} Versuchen fehlgeschlagen: {e}")

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
    
    folder_path = folder_path.strip()
    if not folder_path.startswith('/'):
        folder_path = '/' + folder_path
    
    parts = [p for p in folder_path.split('/') if p]
    
    current_path = ''
    for part in parts:
        current_path += '/' + part
        
        url = f'{PCLOUD_API_URL}/createfolderifnotexists'
        params = {
            'auth': auth,
            'path': current_path
        }
        
        response = requests.get(url, params=params, timeout=30)
        result = response.json()
        
        if result.get('result') not in [0, 2004]:
            error = result.get('error', 'Unbekannter Fehler')
            print(f"  ⚠️  Warnung beim Erstellen von '{current_path}': {error}")
        else:
            print(f"  ✓ Ordner erstellt/gefunden: '{current_path}'")
    
    return {'result': 0}

def pcloud_upload(auth, local_file, remote_path):
    """Lade Datei zu PCloud hoch"""
    
    if not remote_path.startswith('/'):
        remote_path = '/' + remote_path
    
    url = f'{PCLOUD_API_URL}/uploadfile'
    
    params = {
        'auth': auth,
        'path': remote_path,
        'filename': os.path.basename(local_file)
    }
    
    try:
        print(f"    Uploade zu: '{remote_path}'")
        print(f"    Dateiname: '{os.path.basename(local_file)}'")
        
        with open(local_file, 'rb') as f:
            files = {'file': f}
            response = requests.post(url, params=params, files=files, timeout=300)
        
        result = response.json()
        
        if result.get('result') != 0:
            print(f"  ❌ PCloud Upload Fehler:")
            print(f"     Error Code: {result.get('result')}")
            print(f"     Error Message: {result.get('error')}")
        
        return result
    except Exception as e:
        print(f"  ❌ Exception beim Upload: {e}")
        return {'result': -1, 'error': str(e)}

def main():
    print("🚀 Starte YouTube to PCloud Sync...")
    
    if not check_ffmpeg():
        sys.exit(1)
    
    if not all([PLAYLIST_ID, PCLOUD_USER, PCLOUD_PASS]):
        print("❌ Fehlende Umgebungsvariablen!")
        print(f"   PLAYLIST_ID: {'✓' if PLAYLIST_ID else '✗'}")
        print(f"   PCLOUD_USERNAME: {'✓' if PCLOUD_USER else '✗'}")
        print(f"   PCLOUD_PASSWORD: {'✓' if PCLOUD_PASS else '✗'}")
        sys.exit(1)
    
    setup_cookies()
    
    downloaded = load_downloaded_videos()
    print(f"📋 {len(downloaded)} Videos bereits heruntergeladen")
    
    print(f"🔍 Suche neue Videos in Playlist {PLAYLIST_ID}...")
    playlist_videos = get_playlist_videos()
    
    if not playlist_videos:
        print("❌ Keine Videos in Playlist gefunden!")
        sys.exit(1)
    
    print(f"📺 {len(playlist_videos)} Videos in Playlist gefunden")
    
    new_videos = [(vid, title) for vid, title in playlist_videos if vid not in downloaded]
    
    if not new_videos:
        print("✅ Keine neuen Videos gefunden!")
        return
    
    print(f"🆕 {len(new_videos)} neue Videos gefunden!")
    
    print("🔐 Authentifiziere bei PCloud...")
    auth = pcloud_auth()
    
    pcloud_create_folder(auth, PCLOUD_FOLDER)
    
    success_count = 0
    failed_videos = []
    
    for i, (video_id, title) in enumerate(new_videos, 1):
        try:
            print(f"\n📥 [{i}/{len(new_videos)}] Lade herunter: {title}")
            local_file = download_video(video_id, title)
            
            print(f"☁️  Lade hoch zu PCloud...")
            result = pcloud_upload(auth, local_file, PCLOUD_FOLDER)
            
            if result.get('result') == 0:
                print(f"✅ Erfolgreich: {title}")
                save_downloaded_video(video_id)
                success_count += 1
                
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