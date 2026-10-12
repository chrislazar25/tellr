from experiments.failure_hunt.hunt import run, tts, gen_noise, run_confirm
V={"roger_us":"CwhRBWXzGAHq8TQ4Fs17","charlie_au":"IKne3meq5aSn9XLyUdCD","alice_uk":"Xb7hH8MSUJpSbSDYk0k2"}
L=[("fourteen","Pay Demo Paper Supply fourteen dollars to account ending zero zero one.",{"amount":14,"acct":"001"}),
   ("forty","Pay Demo Paper Supply forty dollars to account ending zero zero one.",{"amount":40,"acct":"001"}),
   ("seventeen_hundred","Pay Demo Equipment seventeen hundred dollars to account ending zero zero three.",{"amount":1700,"acct":"003"}),
   ("seventy","Pay Demo Equipment seventy dollars to account ending zero zero three.",{"amount":70,"acct":"003"}),
   ("nineteen","Pay Demo Catering nineteen dollars to account ending two four.",{"amount":19,"acct":"24"}),
   ("ninety","Pay Demo Catering ninety dollars to account ending two four.",{"amount":90,"acct":"24"})]

def main():
    for case,text,truth in L:
      for vn,vid in V.items():
        for sp in (1.0,1.15):
          for ch in ["phone_snr10","phone_snr5","phone_snr0"]:
            r=run(case,text,truth,vid,sp,ch); print(case,vn,sp,ch,{k:(v['amount'],v['acct']) for k,v in r['results'].items()},flush=True)
    # confirmations
    C=[("yes","Yes, go ahead.",True),("no","No, don't send it.",False),("hold","Uhh, hold on, let me check.",False)]
    for case,text,aff in C:
      for vn,vid in V.items():
        src=tts(text,vid)
        for ch in ["clean","phone_snr5","phone_snr0"]:
          r=run_confirm(case,src,aff,ch); print("CONFIRM",case,vn,ch,{k:v['text'] for k,v in r['results'].items()},flush=True)
    for kind in ("silence","hiss","rustle"):
      for ch in ["clean","phone_snr10"]:
        r=run_confirm(kind,gen_noise(kind),False,ch); print("CONFIRM",kind,ch,{k:(v['text'],v.get('no_speech_prob')) for k,v in r['results'].items()},flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
