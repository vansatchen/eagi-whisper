#!/usr/bin/env python3

import os, time
import asyncio
import websockets
import concurrent.futures
import wave
import subprocess
import json
import syslog
import shlex
from faster_whisper import WhisperModel

modelSize = "large-v3"
#model = WhisperModel("large-v3", device="cuda", compute_type="float32") # Use for old videocard
model = WhisperModel("large-v3", device="cuda", compute_type="float16")
lockFile = '/tmp/asr-busy.lock'

def process_chunk(rec, message, initial_prompt):
    global pid
    if message == '{"eof" : 1}':
        # ffmpeg for remove noises and loud normalization
        ffmpegCommand = f'ffmpeg -y -hide_banner -loglevel error -nostdin -threads 0 -i /tmp/whisper-asterisk-{pid}.wav -af \
                         "silenceremove=start_periods=1\
                          :stop_periods=-1\
                          :start_threshold=-50dB\
                          :stop_threshold=-50dB\
                          :start_silence=0.1\
                          :stop_silence=0.1, loudnorm" \
                          -f wav -ac 1 -acodec pcm_s16le -ar 16000 /tmp/whisper-gained-{pid}.wav'
        subprocess.check_output(ffmpegCommand, shell=True)
        segments, info = model.transcribe(f'/tmp/whisper-gained-{pid}.wav', beam_size=5, language='ru', without_timestamps=True)
        result = ''
        for segment in segments:
            result = result + segment.text
        symbol = result.find('[')
        if symbol >= 0: result = result[:symbol]
        rec = '{"result" : [{"model" : "' + modelSize + '"}], "text" : "' + result + '"}'
        os.remove(f'/tmp/whisper-asterisk-{pid}.wav')
        os.remove(f'/tmp/whisper-gained-{pid}.wav')
        return rec, True
    else:
        return rec, False

async def recognize(websocket):
    global pool
    global pid

    loop = asyncio.get_running_loop()
    wavData = []
    print('Connection from', websocket.remote_address[0])
    syslog.syslog(syslog.LOG_INFO, f'Connection from {websocket.remote_address[0]}')

    count = 0
    pid = ""
    while True:
        try:
            message = await websocket.recv()
        except:
            break
        prompt = ""
        if isinstance(message, str):
            if 'config' in message:
                jsonConfig = json.loads(message)['config']
                if 'count' in jsonConfig:
                    askCount = jsonConfig['count']
                if 'prompt' in jsonConfig:
                    prompt = jsonConfig['prompt']
                if 'pid' in jsonConfig:
                    pid = jsonConfig['pid']
                continue
            elif 'status' in message:
                if os.path.isfile(lockFile):
                    with open(lockFile, 'r') as file:
                        createTime = int(file.readlines()[0].splitlines()[0])
                        if int(time.time()) - createTime > 180:
                            await websocket.send('{"busy" : "0"}')
                            os.remove(lockFile)
                        else:
                            await websocket.send('{"busy" : "1"}')
                            print('Locked')
                            syslog.syslog(syslog.LOG_INFO, 'Locked')
                else:
                    await websocket.send('{"busy" : "0"}')
                break
        else:
            wavData.append(message)
            if not os.path.isfile(lockFile):
                with open(lockFile, 'w') as file:
                    file.write(str(int(time.time())))

        output = wave.open(f"/tmp/whisper-asterisk-{pid}.wav", 'wb')
        output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        for frames in range(len(wavData)):
            output.writeframes(wavData[frames])
        output.close()

        # Detect silence at the end of speech
        ffmpegCommand = f'ffmpeg -hide_banner -i /tmp/whisper-asterisk-{pid}.wav -af silencedetect=n=-30dB:d=3 -f null - 2>&1'
        result = subprocess.check_output(ffmpegCommand, shell=True)
        for line in result.decode("utf8").split('\n'):
            if 'silence_start' in line:
                print("Silence detected! Stoping recognize!")
                response, stop = await loop.run_in_executor(pool, process_chunk, '{"partial":"counting"}', '{"eof" : 1}', prompt)
                print(response)
                syslog.syslog(syslog.LOG_INFO, response)
                await websocket.send(response[:-1] + ', "stopit" : "1"}')

        response, stop = await loop.run_in_executor(pool, process_chunk, '{"partial":"' + modelSize + '"}', message, prompt)
        if count == askCount:
            response, stop = await loop.run_in_executor(pool, process_chunk, '{"partial":"counting"}', '{"eof" : 1}', prompt)
        else:
            count += 1
        if "result" in response: print(response)
        await websocket.send(response)
        if stop:
            if os.path.isfile(lockFile):
                os.remove(lockFile)
            count = 0
            wavData = []

async def start():
    global pool
    print("Listening on 0.0.0.0:2700")
    pool = concurrent.futures.ThreadPoolExecutor((os.cpu_count() or 1))
    async with websockets.serve(recognize, '0.0.0.0', 2700):
        await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(start())
