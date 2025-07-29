from flask import Flask, Response, jsonify
import os
import glob
import time
import threading
import logging
import json
from pyngrok import ngrok, conf, exception

app = Flask(__name__)

# Configuración de música (ruta corregida)
MUSIC_FOLDER = '/musica'
CHUNK_SIZE = 1024 * 16  # 16KB chunks
BUFFER_SIZE = 1024 * 1024 * 5  # 5MB buffer
FIXED_BITRATE = 160000  # 160 kbps
BYTES_PER_SECOND = FIXED_BITRATE // 8

# Configurar logging para Android
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('radio')

# Estado global
class RadioState:
    __slots__ = ('buffer', 'position', 'song_index', 'playlist',
                 'active', 'lock', 'skip_requested', 'current_song',
                 'is_playing', 'last_song', 'song_map', 'force_change',
                 'tunnel_url')
    
    def __init__(self):
        self.active = False
        self.buffer = bytearray(BUFFER_SIZE)
        self.position = 0
        self.song_index = 0  # Índice base 0
        self.playlist = []
        self.song_map = {}
        self.skip_requested = False
        self.current_song = ""
        self.is_playing = False
        self.last_song = ""
        self.lock = threading.Lock()
        self.force_change = False
        self.tunnel_url = ""

state = RadioState()

def get_audio_chunks(file_path):
    """Generador de chunks de audio optimizado"""
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logger.error(f"Error leyendo {file_path}: {str(e)}")

def update_playlist():
    """Actualiza la lista de reproducción"""
    # Usando la ruta corregida /musica
    new_playlist = sorted(glob.glob(os.path.join(MUSIC_FOLDER, '*.mp3')))
    with state.lock:
        state.playlist = new_playlist
        state.song_map = {index: os.path.basename(song) 
                          for index, song in enumerate(new_playlist)}
        
        if state.playlist and 0 <= state.song_index < len(state.playlist):
            current_song = state.playlist[state.song_index]
            if current_song not in new_playlist:
                state.song_index = 0

def play_song(song_number):
    """Reproduce una canción específica"""
    song_index = song_number - 1  # Base 1 a base 0
    with state.lock:
        if 0 <= song_index < len(state.playlist):
            state.song_index = song_index
            state.skip_requested = True
            state.force_change = True
            state.last_song = ""
            return True, song_index
    return False, -1

def broadcaster():
    """Transmisor principal optimizado"""
    state.active = True
    logger.info("Broadcaster iniciado")
    
    while state.active:
        update_playlist()
        files = state.playlist
        
        if not files:
            logger.warning("No se encontraron archivos MP3 en /musica")
            time.sleep(5)
            continue
        
        with state.lock:
            force_change = state.force_change
            state.force_change = False
            
            if force_change:
                state.last_song = ""
                logger.info(f"Cambio forzado a canción #{state.song_index + 1}")
        
        with state.lock:
            song_path = files[state.song_index]
            song_name = state.song_map.get(state.song_index, os.path.basename(song_path))
            
            if song_name == state.last_song and not force_change:
                state.song_index = (state.song_index + 1) % len(files)
                continue
                
            state.skip_requested = False
            state.current_song = song_name
            state.is_playing = True
            state.last_song = song_name
        
        logger.info(f"Reproduciendo: {state.current_song} (#{state.song_index + 1})")
        
        for chunk in get_audio_chunks(song_path):
            if not state.active:
                return
                
            with state.lock:
                if state.skip_requested:
                    state.skip_requested = False
                    break
            
            with state.lock:
                pos = int(state.position % BUFFER_SIZE)
                end = pos + len(chunk)
                
                if end > BUFFER_SIZE:
                    part1_size = BUFFER_SIZE - pos
                    state.buffer[pos:pos+part1_size] = chunk[:part1_size]
                    state.buffer[0:len(chunk)-part1_size] = chunk[part1_size:]
                else:
                    state.buffer[pos:end] = chunk
                
                state.position += len(chunk)
            
            time.sleep(max(0, len(chunk) / BYTES_PER_SECOND - 0.005))
        
        with state.lock:
            state.is_playing = False
            state.song_index = (state.song_index + 1) % len(files)
        
        time.sleep(0.3)

@app.route('/')
def stream():
    """Endpoint de streaming optimizado"""
    def generate():
        while not state.active:
            time.sleep(0.1)
        
        with state.lock:
            client_pos = state.position
        
        while state.active:
            with state.lock:
                available = state.position - client_pos
                
                if available >= CHUNK_SIZE:
                    pos = int(client_pos % BUFFER_SIZE)
                    end = pos + CHUNK_SIZE
                    
                    if end > BUFFER_SIZE:
                        part1 = bytes(state.buffer[pos:])
                        part2 = bytes(state.buffer[:end - BUFFER_SIZE])
                        chunk = part1 + part2
                    else:
                        chunk = bytes(state.buffer[pos:end])
                    
                    client_pos += len(chunk)
                else:
                    chunk = b''
            
            if chunk:
                yield chunk
                time.sleep(0.002)  # Optimizado para Android
            else:
                time.sleep(0.01)
    
    headers = {
        'Content-Type': 'audio/mpeg',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'icy-br': '160',
        'icy-name': 'Radio NewGamers',
    }
    return Response(generate(), headers=headers)

@app.route('/list')
def list_songs():
    """Lista de canciones optimizada"""
    update_playlist()
    with state.lock:
        songs_list = [
            {"number": idx + 1, "name": name} 
            for idx, name in state.song_map.items()
        ]
        
        return jsonify({
            "count": len(songs_list),
            "songs": sorted(songs_list, key=lambda x: x['number']),
            "current_song": state.current_song,
            "current_song_number": state.song_index + 1
        })

@app.route('/play/<int:song_number>')
def play_song_endpoint(song_number):
    """Reproduce canción específica"""
    success, index = play_song(song_number)
    if success:
        with state.lock:
            return jsonify({
                "status": "success",
                "message": f"Reproduciendo canción #{song_number}",
                "song": state.song_map.get(index, "")
            })
    return jsonify({
        "status": "error",
        "message": f"Número inválido: {song_number}. Rango válido: 1-{len(state.playlist)}"
    }), 400

@app.route('/next')
def next_song():
    """Siguiente canción optimizado"""
    with state.lock:
        if state.playlist:
            next_index = (state.song_index + 1) % len(state.playlist)
            next_number = next_index + 1
            play_song(next_number)
            return jsonify({
                "status": "success",
                "message": f"Saltando a canción #{next_number}",
                "song": state.song_map.get(next_index, "")
            })
    return jsonify({"status": "error", "message": "No hay canciones disponibles"}), 400

@app.route('/status')
def status():
    """Estado del servidor optimizado"""
    with state.lock:
        return jsonify({
            "current_song": state.current_song,
            "current_song_number": state.song_index + 1,
            "is_playing": state.is_playing,
            "tunnel_url": state.tunnel_url,
            "music_folder": MUSIC_FOLDER,
            "file_count": len(state.playlist),
            "listeners": threading.active_count() - 2
        })

def start_ngrok_tunnel(port):
    """Inicia el túnel ngrok con manejo de errores"""
    try:
        conf.get_default().region = "eu"  # Región europea para mejor ping
        tunnel = ngrok.connect(port, "http", bind_tls=True)
        state.tunnel_url = tunnel.public_url
        logger.info(f"Túnel ngrok creado: {state.tunnel_url}")
        logger.info(f"Radio accesible en: {state.tunnel_url}/stream")
        return True
    except exception.PyngrokNgrokError as e:
        logger.error(f"Error ngrok: {str(e)}")
        return False

if __name__ == '__main__':
    # Verificar existencia de la carpeta /musica
    if not os.path.exists(MUSIC_FOLDER):
        logger.error(f"ERROR: No se encontró el directorio {MUSIC_FOLDER}")
        logger.info("Creando directorio /musica...")
        try:
            os.makedirs(MUSIC_FOLDER)
            logger.info(f"Directorio creado: {MUSIC_FOLDER}")
        except Exception as e:
            logger.error(f"No se pudo crear el directorio: {str(e)}")
            exit(1)
    else:
        logger.info(f"Directorio de música encontrado: {MUSIC_FOLDER}")

    # Contar archivos iniciales
    update_playlist()
    logger.info(f"{len(state.playlist)} archivos MP3 encontrados en /musica")
    
    # Iniciar broadcaster
    threading.Thread(target=broadcaster, daemon=True).start()
    
    # Configurar puerto dinámico
    PORT = 5000
    
    # Iniciar ngrok en segundo plano
    ngrok_thread = threading.Thread(target=start_ngrok_tunnel, args=(PORT,), daemon=True)
    ngrok_thread.start()
    
    # Iniciar servidor Flask optimizado
    logger.info(f"Iniciando servidor en puerto: {PORT}")
    app.run(host='0.0.0.0', port=PORT, threaded=True, use_reloader=False)