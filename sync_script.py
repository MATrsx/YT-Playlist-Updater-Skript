import os
import sys
import json
import requests
import yt_dlp
import subprocess
from pathlib import Path
import time

# Konfiguration aus Umgebungsvariablen
PLAYLIST_ID = os.getenv('YOUTUBE_PLAYLIST_ID')
PCLOUD_USER = os.getenv('PCLOUD_USERNAME')
PCLOUD_PASS = os.getenv('PCLOUD_PASSWORD')
PCLOUD_FOLDER = os.getenv('PCLOUD_FOLDER', '/YouTube')
DOWNLOADED_FILE = 'downloaded_videos.txt'

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

def get_playlist_videos():
    """Hole alle Video IDs aus der Playlist"""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': False
    }
    
    playlist_url = f'https://www.youtube.com/playlist?list={PLAYLIST_ID}'
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        if 'entries' in info:
            return [(entry['id'], entry['title']) for entry in info['entries'] if entry]
    return []

def download_video(video_id, max_retries=3):
    """Lade Video als m4a herunter mit Retry-Mechanismus"""
    
    for attempt in range(max_retries):
        try:
            print(f"  Versuch {attempt + 1}/{max_retries}...")
            
            # Versuch 1-2: Direkt als m4a herunterladen (Audio only)
            if attempt < 2:
                ydl_opts = {
                    'format': 'bestaudio[ext=m4a]/bestaudio',
                    'outtmpl': 'downloads/%(id)s.%(ext)s',
                    'quiet': False,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'm4a',
                    }]
                }
            # Versuch 3: Als MP4 herunterladen und konvertieren
            else:
                print("  Fallback: Lade als MP4 herunter und konvertiere...")
                ydl_opts = {
                    'format': 'bestvideo+bestaudio/best',
                    'outtmpl': 'downloads/%(id)s.%(ext)s',
                    'quiet': False
                }
            
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                
                # Prüfe welche Datei erstellt wurde
                download_dir = Path('downloads')
                possible_files = list(download_dir.glob(f'{video_id}.*'))
                
                if not possible_files:
                    raise Exception("Keine Datei gefunden nach Download")
                
                downloaded_file = str(possible_files[0])
                
                # Wenn nicht m4a, konvertiere es
                if not downloaded_file.endswith('.m4a'):
                    print(f"  Konvertiere {Path(downloaded_file).suffix} zu m4a...")
                    m4a_file = f'downloads/{video_id}.m4a'
                    
                    result = subprocess.run([
                        'ffmpeg', '-i', downloaded_file,
                        '-c:a', 'aac', '-b:a', '192k',
                        '-vn',  # Kein Video
                        '-y',   # Überschreibe falls vorhanden
                        m4a_file
                    ], capture_output=True, text=True)
                    
                    if result.returncode != 0:
                        raise Exception(f"FFmpeg Fehler: {result.stderr}")
                    
                    # Lösche Original
                    os.remove(downloaded_file)
                    downloaded_file = m4a_file
                
                print(f"  ✓ Download erfolgreich: {Path(downloaded_file).name}")
                return downloaded_file
                
        except Exception as e:
            print(f"  ✗ Versuch {attempt + 1} fehlgeschlagen: {e}")
            
            # Cleanup bei Fehler
            download_dir = Path('downloads')
            for f in download_dir.glob(f'{video_id}.*'):
                try:
                    os.remove(f)
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
    url = 'https://api.pcloud.com/userinfo'
    params = {
        'username': PCLOUD_USER,
        'password': PCLOUD_PASS,
        'getauth': 1
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if data.get('result') == 0:
        return data['auth']
    else:
        raise Exception(f"PCloud Auth failed: {data}")

def pcloud_create_folder(auth, folder_path):
    """Erstelle Ordner in PCloud falls nicht vorhanden"""
    url = 'https://api.pcloud.com/createfolderifnotexists'
    params = {
        'auth': auth,
        'path': folder_path
    }
    
    response = requests.get(url, params=params)
    return response.json()

def pcloud_upload(auth, local_file, remote_path):
    """Lade Datei zu PCloud hoch"""
    url = 'https://api.pcloud.com/uploadfile'
    
    params = {
        'auth': auth,
        'path': remote_path,
        'filename': os.path.basename(local_file)
    }
    
    with open(local_file, 'rb') as f:
        files = {'file': f}
        response = requests.post(url, params=params, files=files)
    
    return response.json()

def main():
    print("🚀 Starte YouTube to PCloud Sync...")
    
    # Validiere Umgebungsvariablen
    if not all([PLAYLIST_ID, PCLOUD_USER, PCLOUD_PASS]):
        print("❌ Fehlende Umgebungsvariablen!")
        sys.exit(1)
    
    # Lade bereits heruntergeladene Videos
    downloaded = load_downloaded_videos()
    print(f"📋 {len(downloaded)} Videos bereits heruntergeladen")
    
    # Hole Playlist Videos
    print(f"🔍 Suche neue Videos in Playlist {PLAYLIST_ID}...")
    playlist_videos = get_playlist_videos()
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
    
    # Verarbeite neue Videos
    for video_id, title in new_videos:
        try:
            print(f"\n📥 Lade herunter: {title}")
            local_file = download_video(video_id)
            
            print(f"☁️ Lade hoch zu PCloud...")
            result = pcloud_upload(auth, local_file, PCLOUD_FOLDER)
            
            if result.get('result') == 0:
                print(f"✅ Erfolgreich: {title}")
                save_downloaded_video(video_id)
                
                # Lösche lokale Datei
                os.remove(local_file)
            else:
                print(f"❌ Upload fehlgeschlagen: {result}")
                
        except Exception as e:
            print(f"❌ Fehler bei {title}: {e}")
            continue
    
    print("\n🎉 Sync abgeschlossen!")

if __name__ == '__main__':
    main()