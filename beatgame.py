import time
import threading
import queue

BPM = 60
tDiffer_Beat = 60/BPM
MaxError = tDiffer_Beat/8
clap_input = queue.Queue()##以后改成硬件的digital信号输入
rythem_arr = [4,8,8,4]
isStart = False
isCorrect = True

def preInput():
    while(True):
        try:
            clap = input()
            clap_time = time.perf_counter()
            clap_input.put((clap_time, clap))
        except EOFError:
            break

threading.Thread(target=preInput, daemon=True).start()

def getBeatTime(rythem):
    temp = (1/rythem)*4
    tDiffer = temp*tDiffer_Beat
    return tDiffer

print("Enter R to clap")

i = 0
last = None         
while(i<len(rythem_arr)-1):

    clap = None
    try:
        clap_time,clap = clap_input.get(timeout=0.01)  
    except queue.Empty:
        continue
    
    if(clap != None):
        
        now = clap_time

        if(last == None):
            last = now
            print("第1个节奏开始 True")
            continue

        duration = now - last
        
        BeatTime = 0
        for t in range(i+1):
            BeatTime = BeatTime + getBeatTime(rythem_arr[t])

        if(duration >= BeatTime-MaxError and duration <= BeatTime+MaxError):
            isCorrect = True
        else:
            isCorrect = False
        print("第%d个节奏结束  间隔=%.3fs  目标=%.3fs 误差允许范围=%.3f %s 第%d个节奏开始" 
              % (i+1, duration, BeatTime, MaxError, isCorrect, i+2))   
        
        i += 1          



