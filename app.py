from flask import Flask, Response, jsonify
import os
import glob
import time
import threading
import logging
import json

app = Flask(__name__)
MUSIC_FOLDER = '/musica '

# Configuración fija
CHUNK_SIZE = 1024 * 16  # 16KB chunks
BUFFER_SIZE = 1024 * 1024 * 5  # 5MB buffer fijo
FIXED_BITRATE = 160000  # 160 kbps fijo
BYTES_PER_SECOND = FIXED_BITRATE // 8

# Estado global
class RadioState:
    __slots__ = ('buffer', 'position', 'song_index', 'playlist',
                 'active', 'lock', 'skip_requested', 'current_song',
                 'is_playing', 'last_song', 'song_map', 'force_change')
    
    def __init__(self):
        self.active = False
        self.buffer = bytearray(BUFFER_SIZE)
        self.position = 0
        self.song_index = 0  # Índice interno (base 0)
        self.playlist = []
        self.song_map = {}  # Mapa de índice a nombre de archivo
        self.skip_requested = False
        self.current_song = ""
        self.is_playing = False
        self.last_song = ""
        self.lock = threading.Lock()
        self.force_change = False

# Instancia única de estado
state = RadioState()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('radio')

def get_audio_chunks(filepath):
    """Generador simple de chunks de audio"""
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logger.error(f"Error reading {filepath}: {str(e)}")

def update_playlist():
    """Actualiza la lista de reproducción y el mapa de canciones"""
    new_playlist = sorted(glob.glob(os.path.join(MUSIC_FOLDER, '*.mp3')))
    with state.lock:
        # Actualizar playlist
        state.playlist = new_playlist
        
        # Crear mapa de canciones: índice base 0 -> nombre base
        state.song_map = {index: os.path.basename(song) 
                          for index, song in enumerate(new_playlist)}
        
        # Mantener índice actual si es posible
        if state.playlist and 0 <= state.song_index < len(state.playlist):
            current_song = state.playlist[state.song_index]
            if current_song not in new_playlist:
                state.song_index = 0

def play_song(song_number):
    """Fuerza la reproducción de una canción específica por número (base 1)"""
    # Convertir número de canción (base 1) a índice interno (base 0)
    song_index = song_number - 1
    
    with state.lock:
        if 0 <= song_index < len(state.playlist):
            state.song_index = song_index
            state.skip_requested = True
            state.force_change = True
            state.last_song = ""
            return True, song_index
    return False, -1

def broadcaster():
    """Transmisor principal con manejo de cambios forzados"""
    state.active = True
    logger.info("Broadcaster started")
    
    while state.active:
        update_playlist()
        files = state.playlist
        
        if not files:
            time.sleep(1)
            continue
        
        # Manejar cambio forzado de canción
        with state.lock:
            force_change = state.force_change
            state.force_change = False
            
            if force_change:
                state.last_song = ""
                logger.info(f"Forcing change to song #{state.song_index + 1}")
        
        with state.lock:
            # Obtener canción actual
            song_path = files[state.song_index]
            song_name = state.song_map.get(state.song_index, os.path.basename(song_path))
            
            # Solo procesar si es una canción nueva
            if song_name == state.last_song and not force_change:
                # Avanzar a la siguiente canción
                state.song_index = (state.song_index + 1) % len(files)
                continue
                
            state.skip_requested = False
            state.current_song = song_name
            state.is_playing = True
            state.last_song = song_name
        
        # Mostrar información de la canción con número (base 1)
        logger.info(f"Playing: {state.current_song} (Song #{state.song_index + 1})")
        
        # Transmitir canción
        for chunk in get_audio_chunks(song_path):
            if not state.active:
                return
                
            # Verificar solicitud de salto
            with state.lock:
                if state.skip_requested:
                    state.skip_requested = False
                    break
            
            # Escribir en buffer circular
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
            
            # Control de tiempo fijo
            time.sleep(len(chunk) / BYTES_PER_SECOND)
        
        # Finalizar canción
        with state.lock:
            state.is_playing = False
            # Avanzar a la siguiente canción
            state.song_index = (state.song_index + 1) % len(files)
        
        # Pequeña pausa entre canciones
        time.sleep(0.5)

@app.route('/')
def stream():
    """Endpoint de streaming"""
    def generate():
        while not state.active:
            time.sleep(0.1)
        
        with state.lock:
            client_pos = state.position
        
        while state.active:
            with state.lock:
                available = state.position - client_pos
                
                if available >= CHUNK_SIZE:
                    # Leer chunk
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
            
            # Entregar chunk si hay datos
            if chunk:
                yield chunk
                # Pequeña pausa para evitar saturación
                time.sleep(0.005)
            else:
                time.sleep(0.01)
    
    headers = {
        'Content-Type': 'audio/mpeg',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'icy-br': '160',
        'icy-name': 'Radio Selector',
    }
    return Response(generate(), headers=headers)

@app.route('/list')
def list_songs():
    """Devuelve la lista de canciones con números (base 1)"""
    update_playlist()
    with state.lock:
        # Crear lista con números (base 1) y nombres
        songs_list = [
            {"number": index,"name": name} 
            for index, name in state.song_map.items()
        ]
        
        # Ordenar por número
        songs_list.sort(key=lambda x: x['number'])
        
        return jsonify({
            "count": len(songs_list),
            "songs": songs_list,
            "current_song_number": state.song_index,
            "current_song": state.current_song
        })

@app.route('/play/<int:song_number>')
def play_song_endpoint(song_number):
    """Reproduce una canción específica por número (base 1)"""
    success, index = play_song(song_number)
    if success:
        with state.lock:
            return jsonify({
                "status": "success",
                "message": f"Playing song #{song_number}",
                "song": state.song_map.get(index+1, "")
            })
    return jsonify({
        "status": "error",
        "message": f"Invalid song number: {song_number}. Valid range: 1-{len(state.playlist)}"
    }), 400

@app.route('/next')
def next_song():
    """Avanza a la siguiente canción"""
    with state.lock:
        if state.playlist:
            next_index = (state.song_index + 1) % len(state.playlist)
            next_number = next_index + 1
            play_song(next_number)
            return jsonify({
                "status": "success",
                "message": f"Skipping to next song #{next_number}",
                "song": state.song_map.get(next_index, "")
            })
    return jsonify({"status": "error", "message": "No songs available"}), 400

@app.route('/next/<int:song_number>')
def next_specific_song(song_number):
    """Salta a una canción específica por número (base 1)"""
    success, index = play_song(song_number)
    if success:
        with state.lock:
            return jsonify({
                "status": "success",
                "message": f"Playing song #{song_number}",
                "song": state.song_map.get(index, "")
            })
    return jsonify({
        "status": "error",
        "message": f"Invalid song number: {song_number}. Valid range: 1-{len(state.playlist)}"
    }), 400

@app.route('/status')
def status():
    """Estado del servidor"""
    with state.lock:
        return jsonify({
            "current_song": state.current_song,
            "current_song_number": state.song_index + 1,
            "is_playing": state.is_playing,
            "position": state.position,
            "listeners": threading.active_count() - 2
        })

if __name__ == '__main__':
    # Inicialización
    update_playlist()
    
    # Iniciar broadcaster
    threading.Thread(target=broadcaster, daemon=True).start()
    
    # Ejecutar servidor
    logger.info(f"Radio server ready at http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
