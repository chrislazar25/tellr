from experiments.failure_hunt.hunt import run
L=[("magnitude","Pay Demo Equipment fifteen hundred dollars to account ending zero zero three.",{"amount":1500,"acct":"003"}),
   ("thirteen","Pay Demo Paper Supply thirteen dollars to account ending zero zero one.",{"amount":13,"acct":"001"}),
   ("fifteen","Pay Demo Paper Supply fifteen dollars to account ending zero zero one.",{"amount":15,"acct":"001"}),
   ("fifty","Pay Demo Paper Supply fifty dollars to account ending zero zero one.",{"amount":50,"acct":"001"}),
   ("acct24","Pay Demo Catering fifty dollars to account ending two four.",{"amount":50,"acct":"24"}),
   ("eighteen13","Pay Demo Catering eighteen dollars to account ending one three.",{"amount":18,"acct":"13"})]
V={"roger_us":"CwhRBWXzGAHq8TQ4Fs17","charlie_au":"IKne3meq5aSn9XLyUdCD","alice_uk":"Xb7hH8MSUJpSbSDYk0k2"}

def main():
    for case,text,truth in L:
      for vn,vid in V.items():
        for ch in ["clean","phone_snr10","phone_snr5","phone_snr0"]:
          r=run(case,text,truth,vid,1.0,ch)
          f={k:(v["fail"],v["amount"],v["acct"]) for k,v in r["results"].items() if v["fail"]}
          print(case,vn,ch,"FAIL" if f else "ok",f if f else "",flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
