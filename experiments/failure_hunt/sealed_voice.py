"""Sealed test: one fresh voice, fixed scripts/channels from rounds 1-2, no selection. Logged separately."""
from pathlib import Path

from experiments.failure_hunt import hunt
VOICE = ("bill_us_old", "pqHfZKP75CvOlQylNhV4")
R1 = [("magnitude","Pay Demo Equipment fifteen hundred dollars to account ending zero zero three.",{"amount":1500,"acct":"003"}),
      ("thirteen","Pay Demo Paper Supply thirteen dollars to account ending zero zero one.",{"amount":13,"acct":"001"}),
      ("fifteen","Pay Demo Paper Supply fifteen dollars to account ending zero zero one.",{"amount":15,"acct":"001"}),
      ("fifty","Pay Demo Paper Supply fifty dollars to account ending zero zero one.",{"amount":50,"acct":"001"}),
      ("acct24","Pay Demo Catering fifty dollars to account ending two four.",{"amount":50,"acct":"24"}),
      ("eighteen13","Pay Demo Catering eighteen dollars to account ending one three.",{"amount":18,"acct":"13"})]
R2 = [("fourteen","Pay Demo Paper Supply fourteen dollars to account ending zero zero one.",{"amount":14,"acct":"001"}),
      ("forty","Pay Demo Paper Supply forty dollars to account ending zero zero one.",{"amount":40,"acct":"001"}),
      ("seventeen_hundred","Pay Demo Equipment seventeen hundred dollars to account ending zero zero three.",{"amount":1700,"acct":"003"}),
      ("seventy","Pay Demo Equipment seventy dollars to account ending zero zero three.",{"amount":70,"acct":"003"}),
      ("nineteen","Pay Demo Catering nineteen dollars to account ending two four.",{"amount":19,"acct":"24"}),
      ("ninety","Pay Demo Catering ninety dollars to account ending two four.",{"amount":90,"acct":"24"})]
def main():
    previous_log = hunt.LOG
    hunt.LOG = str(Path(__file__).with_name("sealed_attempts.jsonl"))
    try:
        for case, text, truth in R1:
            for ch in ["clean", "phone_snr10", "phone_snr5", "phone_snr0"]:
                r = hunt.run(case, text, truth, VOICE[1], 1.0, ch); print(case, ch, {k: (v["amount"], v["acct"]) for k, v in r["results"].items()}, flush=True)
        for case, text, truth in R2:
            for sp in (1.0, 1.15):
                for ch in ["phone_snr10", "phone_snr5", "phone_snr0"]:
                    r = hunt.run(case, text, truth, VOICE[1], sp, ch); print(case, sp, ch, {k: (v["amount"], v["acct"]) for k, v in r["results"].items()}, flush=True)
    finally:
        hunt.LOG = previous_log


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
