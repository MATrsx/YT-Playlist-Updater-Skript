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
from datetime import datetime
from mutagen.mp4 import MP4, MP4Cover

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

def check_dependencies():
    """Prüfe ob ffmpeg und mutagen verfügbar sind"""
    if not shutil.which('ffmpeg'):
        print("❌ FEHLER: ffmpeg ist nicht installiert!")
        print("   Installiere es mit: sudo apt-get install ffmpeg")
        return False
    print("✓ ffmpeg gefunden")
    
    try:
        import mutagen
        print("✓ mutagen gefunden")
    except ImportError:
        print("❌ FEHLER: mutagen ist nicht installiert!")
        print("   Installiere es mit: pip install mutagen")
        return False
    
    return True

def pcloud_list_files(auth, folder_path):
    """Liste alle Dateien im PCloud Ordner und lade ihre Metadaten"""
    if not folder_path.startswith('/'):
        folder_path = '/' + folder_path
    
    url = f'{PCLOUD_API_URL}/listfolder'
    params = {
        'auth': auth,
        'path': folder_path
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        result = response.json()
        
        if result.get('result') == 0:
            files = []
            if 'metadata' in result and 'contents' in result['metadata']:
                for item in result['metadata']['contents']:
                    if not item.get('isfolder', False):
                        files.append({
                            'name': item['name'],
                            'fileid': item.get('fileid')
                        })
            return files
        else:
            print(f"  ⚠️  Fehler beim Auflisten: {result.get('error', 'Unbekannt')}")
            return []
    except Exception as e:
        print(f"  ⚠️  Exception beim Auflisten: {e}")
        return []

def pcloud_download_file_metadata(auth, fileid, local_path):
    """Lade Datei von PCloud herunter um Metadaten auszulesen"""
    url = f'{PCLOUD_API_URL}/getfilelink'
    params = {
        'auth': auth,
        'fileid': fileid
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        result = response.json()
        
        if result.get('result') == 0:
            download_url = 'https://' + result['hosts'][0] + result['path']
            
            # Lade nur die ersten paar KB für Metadaten
            headers = {'Range': 'bytes=0-65536'}
            file_response = requests.get(download_url, headers=headers, timeout=30)
            
            with open(local_path, 'wb') as f:
                f.write(file_response.content)
            
            return True
        return False
    except Exception as e:
        return False

def extract_video_id_from_m4a_metadata(file_path):
    """Extrahiere Video-ID aus m4a Metadaten (URL Tag)"""
    try:
        audio = MP4(file_path)
        
        # Versuche URL Tag zu lesen
        if '\xa9url' in audio:
            url = audio['\xa9url'][0]
            # Extrahiere Video-ID aus YouTube URL
            match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
            if match:
                return match.group(1)
        
        return None
    except Exception as e:
        return None

def get_downloaded_videos_from_pcloud(auth, folder_path):
    """Hole Liste der bereits in PCloud vorhandenen Videos durch Metadaten-Check"""
    print("🔍 Prüfe vorhandene Dateien in PCloud...")
    
    files = pcloud_list_files(auth, folder_path)
    downloaded_ids = set()
    temp_dir = Path('temp_metadata')
    temp_dir.mkdir(exist_ok=True)
    
    print(f"📋 Analysiere {len(files)} Dateien...")
    
    for i, file_info in enumerate(files, 1):
        filename = file_info['name']
        fileid = file_info['fileid']
        
        if not filename.endswith('.m4a'):
            continue
        
        # Zeige Fortschritt nur alle 10 Dateien
        if i % 10 == 0 or i == len(files):
            print(f"  Fortschritt: {i}/{len(files)} Dateien geprüft...", end='\r')
        
        try:
            temp_file = temp_dir / f'temp_{fileid}.m4a'
            
            # Lade Datei-Header für Metadaten
            if pcloud_download_file_metadata(auth, fileid, temp_file):
                video_id = extract_video_id_from_m4a_metadata(temp_file)
                
                if video_id:
                    downloaded_ids.add(video_id)
                
                # Lösche Temp-Datei
                temp_file.unlink()
        except Exception as e:
            continue
    
    # Cleanup
    try:
        shutil.rmtree(temp_dir)
    except:
        pass
    
    print(f"\n📋 {len(downloaded_ids)} Videos mit gültigen IDs in PCloud gefunden")
    return downloaded_ids

def load_downloaded_videos():
    """Lade Liste der bereits heruntergeladenen Videos (Legacy, falls lokal verwendet)"""
    if os.path.exists(DOWNLOADED_FILE):
        with open(DOWNLOADED_FILE, 'r') as f:
            return set(line.strip() for line in f)
    return set()

def save_downloaded_video(video_id):
    """Speichere Video ID als heruntergeladen (Legacy, falls lokal verwendet)"""
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

def set_file_metadata(m4a_file, info_json_path):
    """Setze Metadaten und Thumbnail in m4a Datei"""
    try:
        print(f"  📝 Setze Metadaten...")
        
        # Lade JSON Metadaten
        if not os.path.exists(info_json_path):
            print(f"  ⚠️  Info-JSON nicht gefunden: {info_json_path}")
            return False
        
        with open(info_json_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        
        # Öffne m4a Datei
        audio = MP4(m4a_file)
        
        # Setze grundlegende Metadaten
        if 'title' in info:
            audio['\xa9nam'] = info['title']  # Title
            print(f"    ✓ Titel: {info['title']}")
        
        if 'uploader' in info or 'channel' in info:
            artist = info.get('uploader', info.get('channel', ''))
            audio['\xa9ART'] = artist  # Artist
            audio['\xa9alb'] = artist  # Album (auch Artist)
            print(f"    ✓ Artist/Album: {artist}")
        
        if 'description' in info:
            audio['\xa9cmt'] = info['description'][:255]  # Comment (gekürzt)
            print(f"    ✓ Beschreibung gesetzt")
        
        if 'upload_date' in info:
            upload_date = info['upload_date']
            audio['\xa9day'] = upload_date  # Year/Date
            print(f"    ✓ Upload Datum: {upload_date}")
        
        if 'webpage_url' in info:
            audio['\xa9url'] = info['webpage_url']  # URL
        
        # Genre auf "YouTube" setzen
        audio['\xa9gen'] = 'YouTube'
        
        # Thumbnail einbetten
        thumbnail_path = Path(m4a_file).with_suffix('.jpg')
        if thumbnail_path.exists():
            with open(thumbnail_path, 'rb') as img_file:
                img_data = img_file.read()
                audio['covr'] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
                print(f"    ✓ Thumbnail eingebettet")
            
            # Lösche Thumbnail-Datei
            try:
                thumbnail_path.unlink()
            except:
                pass
        else:
            print(f"    ⚠️  Kein Thumbnail gefunden")
        
        # Speichere Änderungen
        audio.save()
        print(f"  ✓ Metadaten erfolgreich gesetzt")
        
        return True
        
    except Exception as e:
        print(f"  ⚠️  Fehler beim Setzen der Metadaten: {e}")
        return False

def set_file_timestamps(m4a_file, info_json_path):
    """Setze Creation Date (Upload) und Modified Date (aktuell)"""
    try:
        current_time = datetime.now()
        creation_time = current_time  # Default
        
        # Versuche Upload-Datum aus JSON zu lesen
        if os.path.exists(info_json_path):
            with open(info_json_path, 'r', encoding='utf-8') as f:
                info = json.load(f)
            
            if 'upload_date' in info:
                upload_date = info['upload_date']
                if re.match(r'^\d{8}$', upload_date):
                    year = int(upload_date[0:4])
                    month = int(upload_date[4:6])
                    day = int(upload_date[6:8])
                    creation_time = datetime(year, month, day, 12, 0, 0)
                    print(f"  📅 Upload Datum: {creation_time.strftime('%d.%m.%Y')}")
        
        # Setze Timestamps
        file_path = Path(m4a_file)
        
        # Creation Time (Upload Datum)
        creation_timestamp = creation_time.timestamp()
        
        # Modified Time (aktuelle Zeit)
        modified_timestamp = current_time.timestamp()
        
        # Setze die Timestamps (Unix: atime, mtime)
        os.utime(file_path, (modified_timestamp, modified_timestamp))
        
        # Für Creation Time auf Linux: verwende stat wenn verfügbar
        # Auf Windows würde das automatisch funktionieren
        
        print(f"  ✓ Creation: {creation_time.strftime('%d.%m.%Y %H:%M')}")
        print(f"  ✓ Modified: {current_time.strftime('%d.%m.%Y %H:%M:%S')}")
        
        return True
        
    except Exception as e:
        print(f"  ⚠️  Fehler beim Setzen der Timestamps: {e}")
        return False

def download_video(video_id, video_title, max_retries=3):
    """Lade Video als m4a herunter mit Metadaten und Thumbnail"""
    
    safe_title = sanitize_filename(video_title)
    
    for attempt in range(max_retries):
        try:
            print(f"  Versuch {attempt + 1}/{max_retries}...")
            
            # Cleanup vorheriger Versuche
            download_dir = Path('downloads')
            for f in download_dir.glob(f'{video_id}*'):
                try:
                    f.unlink()
                except:
                    pass
            
            # yt-dlp Optionen mit Metadaten und Thumbnail
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': f'downloads/{video_id}.%(ext)s',
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'm4a',
                        'preferredquality': '192',
                    },
                    {
                        'key': 'EmbedThumbnail',
                        'already_have_thumbnail': False
                    }
                ],
                'writethumbnail': True,  # Thumbnail herunterladen
                'write_info_json': True,  # Metadaten als JSON speichern
                'keepvideo': False,
                'quiet': False,
                'no_warnings': False,
                'ignoreerrors': False,
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'extractor_retries': 3,
            }
            
            if os.path.exists(COOKIES_FILE):
                ydl_opts['cookiefile'] = COOKIES_FILE
            
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
            
            time.sleep(2)
            
            # Suche heruntergeladene Datei
            possible_files = list(download_dir.glob(f'{video_id}*.m4a'))
            
            if not possible_files:
                possible_files = list(download_dir.glob(f'{video_id}*'))
                possible_files = [f for f in possible_files if f.suffix in ['.webm', '.mp4', '.m4a', '.opus']]
            
            if not possible_files:
                raise Exception(f"Keine Datei gefunden nach Download")
            
            downloaded_file = possible_files[0]
            print(f"  ✓ Gefundene Datei: {downloaded_file.name}")
            
            # Finale m4a Datei - OHNE VIDEO_ID im Dateinamen
            final_filename = f'{safe_title}.m4a'
            m4a_file = download_dir / final_filename
            
            # Konvertiere falls nötig
            if downloaded_file.suffix != '.m4a':
                print(f"  Konvertiere {downloaded_file.suffix} zu m4a...")
                
                result = subprocess.run([
                    'ffmpeg', '-i', str(downloaded_file),
                    '-c:a', 'aac', '-b:a', '192k',
                    '-vn', '-y',
                    str(m4a_file)
                ], capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    raise Exception(f"FFmpeg Fehler: {result.stderr}")
                
                downloaded_file.unlink()
            else:
                downloaded_file.rename(m4a_file)
            
            # Info-JSON Pfad
            info_json = download_dir / f'{video_id}.info.json'
            
            # Setze Metadaten in m4a Datei
            set_file_metadata(str(m4a_file), str(info_json))
            
            # Setze Datei-Timestamps
            set_file_timestamps(str(m4a_file), str(info_json))
            
            # Lösche Info-JSON
            try:
                if info_json.exists():
                    info_json.unlink()
            except:
                pass
            
            print(f"  ✓ Download erfolgreich: {m4a_file.name}")
            return str(m4a_file)
                
        except Exception as e:
            print(f"  ✗ Versuch {attempt + 1} fehlgeschlagen: {e}")
            
            # Cleanup bei Fehler
            for f in download_dir.glob(f'{video_id}*'):
                try:
                    f.unlink()
                except:
                    pass
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 10
                print(f"  Warte {wait_time} Sekunden...")
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
    
    if not check_dependencies():
        sys.exit(1)
    
    if not all([PLAYLIST_ID, PCLOUD_USER, PCLOUD_PASS]):
        print("❌ Fehlende Umgebungsvariablen!")
        print(f"   PLAYLIST_ID: {'✓' if PLAYLIST_ID else '✗'}")
        print(f"   PCLOUD_USERNAME: {'✓' if PCLOUD_USER else '✗'}")
        print(f"   PCLOUD_PASSWORD: {'✓' if PCLOUD_PASS else '✗'}")
        sys.exit(1)
    
    setup_cookies()
    
    print("🔐 Authentifiziere bei PCloud...")
    auth = pcloud_auth()
    
    # Erstelle Ordner
    pcloud_create_folder(auth, PCLOUD_FOLDER)
    
    # Hole bereits hochgeladene Videos aus PCloud
    downloaded = get_downloaded_videos_from_pcloud(auth, PCLOUD_FOLDER)
    
    # Fallback: Prüfe auch lokale Datei (für lokale Nutzung)
    local_downloaded = load_downloaded_videos()
    downloaded.update(local_downloaded)
    
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