#!/usr/bin/env python3
"""Trainiert den Gefuehls-Klassifikator (Satz -> Stimmung) und exportiert ihn fuer die Pi.

    python3 tools/train_mood.py [ausgabe-ordner]     (Standard: build/mood)

Laeuft auf dem Entwicklungsrechner, NICHT auf der Pi: es braucht torch, transformers,
onnx, onnxruntime und pyyaml. Die Pi braucht danach nur onnxruntime + tokenizers
(brain/mood.py) und den Ausgabe-Ordner, kopiert nach models/mood/.

Was passiert:
  1. Ein kleines mehrsprachiges BERT (paraphrase-multilingual-MiniLM-L12-v2, ~118 M
     Parameter) wird komplett auf assets/mood/sentences.yaml (train) feinjustiert.
  2. Gemessen wird an den test-Saetzen, die das Training nie sieht. Das Ergebnis steht
     in report.txt im Ausgabe-Ordner -- vor dem Deploy ansehen.
  3. Export nach ONNX, dann dynamische int8-Quantisierung (~4x kleiner, auf der Pi
     schneller). Die quantisierte Fassung wird noch einmal an den test-Saetzen gemessen,
     denn Quantisierung kann Treffer kosten.

Warum dieses Verfahren (gemessen 2026-11-04, 41 ungesehene Saetze, specs/014):
  fertiges deutsches Emotionsmodell (German-Emotions, GoEmotions-Labels) 11/41,
  Qwen2.5 0.5B/1.5B/3B per Prompt 6/16/26, Satz-Aehnlichkeit ohne Training bis 32,
  eingefrorene Embeddings + logistische Regression 34-39, dieses Feintuning 38.
  Situationen ("Ich bin die Treppe runtergefallen") muss das Modell auf UNSERE
  Stimmungen abbilden lernen; das koennen nur Modelle, die darauf trainiert sind.
"""
import json
import os
import random
import re
import sys
from pathlib import Path

import yaml

BASE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EPOCHS = 6
MAX_LEN = 48
HERE = Path(__file__).resolve().parent.parent
NAME = "hubert"


def vosk_form(text):
    """So liefert Vosk einen Satz: klein, ohne Satzzeichen."""
    return " ".join(re.findall(r"[a-zäöüß0-9]+", text.lower()))


def variants(text):
    """Jeder Trainingssatz in den Formen, die die Eule wirklich zu hoeren bekommt."""
    v = vosk_form(text)
    return [text, v, NAME + " " + v, v + " " + NAME]


def main():
    import numpy as np
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "build" / "mood"
    out.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(open(HERE / "assets" / "mood" / "sentences.yaml", encoding="utf-8"))
    labels = list(data["train"].keys())
    lid = {k: i for i, k in enumerate(labels)}
    X = [v for k, xs in data["train"].items() for s in xs for v in variants(s)]
    y = [lid[k] for k, xs in data["train"].items() for s in xs for _ in variants(s)]
    Xt = [s for k, xs in data["test"].items() for s in xs]
    yt = [lid[k] for k, xs in data["test"].items() for _ in xs]

    random.seed(0)
    torch.manual_seed(0)
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(labels), id2label=dict(enumerate(labels)), label2id=lid)
    opt = torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0.01)
    idx = list(range(len(X)))
    for ep in range(EPOCHS):
        model.train()
        random.shuffle(idx)
        for b in range(0, len(idx), 16):
            batch = idx[b:b + 16]
            enc = tok([X[i] for i in batch], padding=True, truncation=True, max_length=MAX_LEN,
                      return_tensors="pt")
            loss = model(**enc, labels=torch.tensor([y[i] for i in batch])).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
        print("Epoche %d/%d" % (ep + 1, EPOCHS), flush=True)
    model.eval()

    report = []

    def score(name, predict):
        # Gemessen in der Form, die Vosk liefert ("hubert ich bin ..."), denn so kommt es an.
        for form, texts in (("Vosk-Form", [NAME + " " + vosk_form(s) for s in Xt]), ("Schreibung", Xt)):
            pred = [predict(s) for s in texts]
            ok = sum(int(p == t) for p, t in zip(pred, yt))
            report.append("%s, %s: %d/%d ungesehene Testsaetze richtig" % (name, form, ok, len(yt)))
            for p, t, s in zip(pred, yt, texts):
                if p != t and form == "Vosk-Form":
                    report.append("   falsch: %-11s -> %-11s %s" % (labels[t], labels[p], s))

    with torch.no_grad():
        score("torch", lambda s: int(model(**tok(s, return_tensors="pt")).logits.argmax(-1)))

    # ONNX-Export (fp32), dann int8. Eingaben: input_ids + attention_mask, dynamische Laenge.
    fp32 = out / "model-fp32.onnx"
    enc = tok("Hallo Hubert", return_tensors="pt")
    torch.onnx.export(model, (enc["input_ids"], enc["attention_mask"]), str(fp32),
                      input_names=["input_ids", "attention_mask"], output_names=["logits"],
                      dynamic_axes={"input_ids": {0: "b", 1: "n"}, "attention_mask": {0: "b", 1: "n"},
                                    "logits": {0: "b"}},
                      opset_version=17)
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(str(fp32), str(out / "model.onnx"), weight_type=QuantType.QInt8)
    fp32.unlink()

    import onnxruntime as ort
    sess = ort.InferenceSession(str(out / "model.onnx"), providers=["CPUExecutionProvider"])

    def onnx_predict(s):
        e = tok(s, return_tensors="np", truncation=True, max_length=MAX_LEN)
        logits = sess.run(None, {"input_ids": e["input_ids"].astype(np.int64),
                                 "attention_mask": e["attention_mask"].astype(np.int64)})[0]
        return int(logits.argmax(-1)[0])

    score("onnx int8", onnx_predict)

    tok.backend_tokenizer.save(str(out / "tokenizer.json"))
    (out / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    size = os.path.getsize(out / "model.onnx") / 1e6
    print("-> %s (model.onnx %.0f MB, tokenizer.json, labels.json, report.txt)" % (out, size))
    print("Deploy: Ordner nach <brain>/models/mood/ kopieren (mood.model_dir in config.yaml).")


if __name__ == "__main__":
    main()
