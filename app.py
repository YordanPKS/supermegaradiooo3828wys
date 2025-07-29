from flask import Flask, Response, jsonify
import os
import glob
import time
import threading
import logging
import socket

app = Flask(__name__)

# Configuraci?n optimizada
MUSIC_FOLDER = '/musica'
CHUNK_SIZE = 1024 * 16
BUFFER_SIZE = 1024 * 1024 * 4
FIXED_BITRATE = 128000
BYTES_PER_SECOND = FIXED_BITRATE // 8

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('radio')

# Estado global corregido
class RadioState:
    __slots__ = ('buffer', 'position', 'song_index', 'playlist',
                 'active', 'lock', 'skip_requested', 'current_song',
                 'is_playing', 'last_song', 'song_map', 'force_change')
    
    def __init__(self):
        self.active = False
        self.buffer = bytearray(BUFFER_SIZE)
        self.position = 0
        self.song_index = 0
        self.playlist = []
        self.song_map = {}
        self.skip_requested = False
        self.current_song = ""
        self.is_playing = False
        self.last_song = ""
        self.lock = threading.Lock()
        self.force_change = False

state = RadioState()

def get_audio_chunks(file_path):
    """Generador de chunks optimizado"""
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logger.error(f"Error leyendo archivo: {str(e)}")
        return []

def update_playlist():
    """Actualiza playlist con detecci?n robusta"""
    try:
        # B?squeda recursiva de archivos MP3
        new_playlist = glob.glob(os.path.join(MUSIC_FOLDER, '**/*.mp3'), recursive=True)
        
        # Orden natural (1.mp3, 2.mp3,... 10.mp3)
        new_playlist.sort(key=lambda x: [int(c) if c.isdigit() else c for c in re.split('(\d+)', x)])
        
        with state.lock:
            # Solo actualizar si hay cambios
            if new_playlist != state.playlist:
                state.playlist = new_playlist
                state.song_map = {index: os.path.basename(song) 
                                  for index, song in enumerate(new_playlist)}
                logger.info(f"Playlist actualizada: {len(new_playlist)} canciones")
                
                # Resetear ?ndice si es necesario
                if state.playlist and state.song_index >= len(state.playlist):
                    state.song_index = 0
    except Exception as e:
        logger.error(f"Error actualizando playlist: {str(e)}")

def play_song(song_number):
    """Reproduce canci?n con verificaci?n robusta"""
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
    """Broadcaster optimizado con detecci?n de m?sica"""
    state.active = True
    logger.info("Iniciando transmision...")
    
    while state.active:
        try:
            update_playlist()
            files = state.playlist
            
            if not files:
                logger.warning("No se encontraron archivos MP3 en /musica")
                time.sleep(5)
                continue
            
            # Manejo de cambio forzado
            with state.lock:
                force_change = state.force_change
                state.force_change = False
                
            # Selecci?n de canci?n
            with state.lock:
                if state.song_index >= len(files):
                    state.song_index = 0
                    
                song_path = files[state.song_index]
                song_name = os.path.basename(song_path)
                state.song_map[state.song_index] = song_name
                
                # Verificar si es nueva canci?n
                if song_name == state.last_song and not force_change:
                    state.song_index = (state.song_index + 1) % len(files)
                    continue
                    
                state.skip_requested = False
                state.current_song = song_name
                state.is_playing = True
                state.last_song = song_name
            
            logger.info(f"Reproduciendo: {song_name} (#{state.song_index + 1})")
            
            # Verificar existencia del archivo
            if not os.path.exists(song_path):
                logger.error(f"Archivo no encontrado: {song_path}")
                with state.lock:
                    state.is_playing = False
                    state.song_index = (state.song_index + 1) % len(files)
                continue
            
            # Transmisi?n con control de tiempo
            byte_counter = 0
            start_time = time.time()
            
            for chunk in get_audio_chunks(song_path):
                if not state.active:
                    return
                    
                # Verificar skip
                with state.lock:
                    if state.skip_requested:
                        state.skip_requested = False
                        break
                
                # Buffering
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
                    byte_counter += len(chunk)
                
                # Control de tiempo adaptativo
                elapsed = time.time() - start_time
                expected = byte_counter / BYTES_PER_SECOND
                sleep_time = max(0, expected - elapsed)
                time.sleep(sleep_time)
            
            # Transici?n entre canciones
            with state.lock:
                state.is_playing = False
                state.song_index = (state.song_index + 1) % len(files)
            
            time.sleep(0.2)
            
        except Exception as e:
            logger.error(f"Error en broadcaster: {str(e)}")
            time.sleep(1)

@app.route('/')
def stream():
    """Streaming optimizado para SA-MP"""
    def generate():
        client_pos = state.position
        first_chunk = True
        
        while not state.active:
            time.sleep(0.1)
        
        while state.active:
            with state.lock:
                available = state.position - client_pos
                
                if available >= CHUNK_SIZE:
                    # Calcular posici?n en buffer circular
                    pos = int(client_pos % BUFFER_SIZE)
                    end = pos + CHUNK_SIZE
                    
                    if end > BUFFER_SIZE:
                        part1 = bytes(state.buffer[pos:])
                        part2 = bytes(state.buffer[:end - BUFFER_SIZE])
                        chunk = part1 + part2
                    else:
                        chunk = bytes(state.buffer[pos:end])
                    
                    client_pos += len(chunk)
                    
                    # Primer chunk lleva metadatos
                    if first_chunk:
                        icy_meta = f"StreamTitle='{state.current_song}';"
                        headers = f"icy-name:SA-MP Radio\r\n" \
                                  f"icy-br:{FIXED_BITRATE // 1000}\r\n" \
                                  f"icy-metaint:{32768}\r\n\r\n"
                        yield headers.encode()
                        first_chunk = False
                    
                    yield chunk
                    time.sleep(0.001)
                else:
                    time.sleep(0.01)
    
    return Response(generate(), content_type='audio/mpeg')

@app.route('/list')
def list_songs():
    """Lista de canciones optimizada"""
    update_playlist()
    with state.lock:
        return jsonify({
            "songs": [{"number": i+1, "name": name} 
                      for i, name in state.song_map.items()],
            "current": state.current_song,
            "playing": state.is_playing
        })

@app.route('/play/<int:song_number>')
def play_song_endpoint(song_number):
    """Reproducir canci?n espec?fica"""
    success, index = play_song(song_number)
    if success:
        with state.lock:
            return jsonify({
                "status": "success",
                "song": state.song_map.get(index, f"Canci?n #{song_number}")
            })
    return jsonify({"status": "error", "message": "N?mero inv?lido"}), 400

@app.route('/next')
def next_song():
    """Siguiente canci?n"""
    with state.lock:
        if state.playlist:
            next_index = (state.song_index + 1) % len(state.playlist)
            play_song(next_index + 1)
            return jsonify({
                "status": "success",
                "song": state.song_map.get(next_index, "")
            })
    return jsonify({"status": "error"}), 400

@app.route('/status')
def status():
    """Estado del servidor"""
    with state.lock:
        return jsonify({
            "song": state.current_song,
            "number": state.song_index + 1,
            "playing": state.is_playing,
            "songs_count": len(state.playlist),
            "buffer_size": f"{state.position / 1024:.1f} KB"
        })

def get_local_ip():
    """Obtiene IP local para acceso en red"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "0.0.0.0"

if __name__ == '__main__':
    # Inicializaci?n
    logger.info("=" * 50)
    logger.info("Iniciando servidor de radio para SA-MP Mobile")
    logger.info("=" * 50)
    
    # Cargar playlist inicial
    update_playlist()
    
    # Iniciar broadcaster en segundo plano
    threading.Thread(target=broadcaster, daemon=True).start()
    
    # Configuraci?n de red
    PORT = 5000
    IP = get_local_ip()
    
    logger.info(f"Directorio de m?sica: {MUSIC_FOLDER}")
    logger.info(f"Archivos detectados: {len(state.playlist)}")
    logger.info(f"URL del stream: http://{IP}:{PORT}/")
    logger.info("Servidor listo para conexiones")
    
    # Iniciar servidor optimizado
    app.run(
        host='0.0.0.0', 
        port=PORT, 
        threaded=True, 
        use_reloader=False,
        debug=False
    )