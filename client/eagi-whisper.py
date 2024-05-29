#!/usr/bin/python3

from asterisk.agi import *
import os, sys, time
from websocket import create_connection
import json
import wave
import mailFunc

pid = os.getpid()
agi = AGI()
arg = None

if len(sys.argv) > 1:
    agi.verbose('AGI Argument: ' + sys.argv[1])
    arg = sys.argv[1]

def playAudio(message):
    wavFile = f'/tmp/rhvoice-{str(pid)}.g722'
    rhvoiceCmd = 'eval dbus-launch > /dev/null; export $(dbus-launch); /bin/echo \"' + message + '\" | \
                  /usr/bin/RHVoice-client -s anna | /usr/bin/ffmpeg -i - -ar 16000 ' + wavFile
    os.system(rhvoiceCmd)
    os.system('killall dbus-daemon')
    agi.exec_command('Playback', wavFile[:-5])
    os.remove(wavFile)
    agi.exec_command('Playback', "silence/1")

def talk(count, prompt):
    global pid
    agi.verbose(prompt)
    ws = create_connection("ws://server.example.com:2700")
    ws.send('{ "config" : { "count" : ' + str(count) + ' , "prompt" : "' + prompt + '" , "pid" : ' + str(pid) + ' } }')
    count += 1
    wavData = []
    try:
        while count > 0:
            data = os.read(3, 8000)
            if data:
                # Record wavfile
                wavData.append(data)
                output = wave.open(f"/tmp/asterisk-{pid}.wav", 'wb')
                output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                for frames in range(len(wavData)):
                    output.writeframes(wavData[frames])
                output.close()

                ws.send_binary(data)
                res = json.loads(ws.recv())
                if 'result' in res:
                    whisperAnswer = res["text"]
                    agi.verbose(f"Result: {whisperAnswer}")
                    break
            count -= 1
    except Exception as err:
        agi.verbose('Problems!!!!')
    finally:
        ws.close()

    return whisperAnswer if whisperAnswer != '' else ''

def startAGI():
    global pid
    agi.verbose("EAGI script started...")
    agi.verbose("Call answered from: %s to %s" % (agi.env['agi_calleridname'], agi.env['agi_extension']))

    # Check that STT-server is available
    try:
        ws = create_connection("ws://server.example.com:2700", timeout=5)
        ws.send('status')
        status = json.loads(ws.recv())
        if 'busy' in status:
            agi.verbose(f'Status: {status["busy"]}')
            agi.set_variable('sttStatus', status["busy"])
    except Exception as err:
        agi.verbose('Check status problems!!!!')
        agi.set_variable('sttStatus', "1")
        return
    finally:
        ws.close()

    # Create pid file
    with open(f'/tmp/eagi-{pid}.pid', 'w') as pidFile:
        pidFile.write(str(int(time.time())))

    agi.verbose(arg)
#    agi.exec_command('Playback', "bot-welcome") # Uses for play static wav files
    playAudio("Здравствуйте! Вы позвонили в службу техподдержки компании рога и копыта. \
               К сожалению, операторы недоступны, поэтому мы примем вашу заявку в автоматическом режиме. \
               После звукового сигнала, представьтесь, назовите свой телефон для связи и кратко опишите проблему. \
               Пожалуйста, дождитесь подтверждения приема заявки.")

    agi.exec_command('Playback', "beep")
    agi.verbose("Connection created")

    problem = talk(800, "")

    # Write message to text file
    try:
        with open(f'/var/calls/messages.txt', 'a') as messageFile:
            messageFile.write(problem + '\n')
    except:
        pass

    playAudio("Спасибо за обращение! Оператор свяжется с вами для уточнения деталей!")
#    agi.exec_command('Playback', "bot-final") # Uses for play static wav files
    if os.path.getsize(f'/tmp/asterisk-{pid}.wav') > 100000:
        mailSubject = f"[СРПЗ] Заявка принята {agi.env['agi_callerid']} {agi.env['agi_calleridname']}"
        mailFunc.sendEmail('admin@example.com', mailSubject, problem, f"/tmp/asterisk-{pid}.wav")
        mailFunc.sendEmail('helpservice@example.com', mailSubject, problem, f"/tmp/asterisk-{pid}.wav")

    os.remove(f"/tmp/asterisk-{pid}.wav")
    os.remove(f"/tmp/eagi-{pid}.pid")

startAGI()
