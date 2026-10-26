# 商品登録の再送・同時実行

- 新しい画面は商品登録ごとにUUIDを発行し、画像を含む送信内容を再送完了までメモリに保持する。
- エラー時は同じページの「保存結果を確認・同じ内容で再送」から再試行する。確認中は入力内容を変更できない。ページを閉じたり強制更新すると入力は失われるため、離脱警告を表示する。
- `GET /item-registrations/{UUID}` は保存完了のみ返す。404は未完了または処理中の可能性があり、同じUUIDでのPOSTはDBの主キー制約で重複を防ぐ。
- `item_registrations` の受付番号・内容ハッシュ・商品IDと商品本体を同一トランザクションで保存する。受付番号を再利用して内容を変更すると409になる。
- 商品を削除しても受付記録は残す（遅延した再送による復活を防ぐ）。デモを全消去する際、受付記録も対象とするなら全端末で登録処理を終了してから削除する。
- 大きいJPEG/PNG/WebPは送信前に長辺1600px以下のJPEGへ縮小（透過部分は白）。小さい画像やその他の形式は元画像を送る。サーバー上限は20MiB。元の端末内の画像は変更しない。
- 同期のStorage/DB操作はFastAPIのワーカースレッド上で実行する。
- StorageとDBは別サービスのため、画像保存後にDBが失敗すると未使用画像が残る可能性はある。商品と売上の重複防止とは別の保守対象。

## 本番DBの追加構造

Supabaseの `add_item_registration_receipts` マイグレーションで適用済み。

```sql
create table public.item_registrations (
  request_id varchar(36) primary key,
  payload_hash varchar(64) not null,
  item_id integer,
  created_at timestamp without time zone not null default (now() at time zone 'utc')
);
alter table public.item_registrations enable row level security;
revoke all on public.item_registrations from anon, authenticated;
```

ブラウザからこのテーブルには直接アクセスさせない。既存のPythonサーバーのDB接続で利用する。

## 検証

`node tests/frontend.test.cjs`

`.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v`

APIテストは専用の一時SQLiteとモックStorageを使用し、本番データを書き換えない。
