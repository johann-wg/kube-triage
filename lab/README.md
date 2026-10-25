# 障害再現ラボ (kind)

`manifests/` の Pod をローカルの kind クラスタに適用し、実際の障害データを収集して評価します。
各 Pod の annotation `triage-lab/expected` が正解ラベルです。

## 必要なもの

- Docker Desktop
- [kind](https://kind.sigs.k8s.io/) と kubectl

## 手順

```bash
kind create cluster --name triage-lab
kubectl apply -f lab/namespace.yaml
kubectl apply -f lab/manifests/

# 障害が安定するまで数分待つ (CrashLoopBackOff は再起動を繰り返すまで時間がかかる)
kubectl -n triage-lab get pods -w

python scripts/collect_lab.py           # scenarios-real/ に保存
kube-triage eval scenarios-real         # 実データで評価

kind delete cluster --name triage-lab   # 後片付け
```

## 実データで分かったこと

- `12-crash-bad-command`: ログは空で、終了理由は `StartError`（終了コード 128）。原因は `lastState.terminated.message` にだけ残る
- `10`〜`13` の CrashLoop 系: 古いコンテナが削除済みだと `kubectl logs --previous` が取得に失敗するため、現在のコンテナのログも併せて取得する必要がある
- `14-init-failure`: `initContainerStatuses` に現れ、メインコンテナは `PodInitializing` のまま

これらはルールと収集処理に反映済みです（[README の評価結果](../README.md#評価結果)）。
合成データでは再現しにくい `registry_auth`（認証エラー）と `taint` はこのラボの対象外です。
