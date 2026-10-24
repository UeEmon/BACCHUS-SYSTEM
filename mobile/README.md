# BACCHUS モバイルアプリ（iOS・Android）

BACCHUSサーバーの共用（テナント）在庫を使う利用者向けのFlutterアプリです。Web版・PWAと同じサーバーAPIを使います。

## できること

- ログイン：メールアドレスとパスワード（多要素認証の確認コード・回復コードに対応）、SSO（OpenID Connect）
- **生体認証ログイン**：一度ログインしたあと端末を登録すると、次回から指紋・顔認証でログイン
- 在庫の一覧・検索・冷蔵庫での絞り込み・空瓶表示、消費・残りを飲み切る・移動
- カメラ撮影（または写真の選択）とAIラベル読み取りによる登録
- 履歴の表示

冷蔵庫・利用者・ログイン方式・AIの管理、初回のパスワード変更、多要素認証の設定、全体管理者の操作はWeb版で行います。通信できないときの操作の保留（Web版の同期機能）は未対応です。

## 生体認証ログインの安全性

1. パスワード（＋確認コード）またはSSOでログインすると、アプリが「生体認証でログインしますか？」と尋ねます。
2. 登録すると、サーバーが端末ごとの**端末トークン**を発行します（サーバーにはハッシュだけを保存）。
3. アプリは端末トークンをOSの保護領域に保存します。
   - iOS：Keychain。`biometryCurrentSet`のアクセス制御付きで、パスコード設定済みの端末だけに保存され、生体情報を登録し直すと読めなくなります。
   - Android：Android Keystoreの鍵で暗号化。クラスStrongの生体認証を必須にしています（Android 9以上）。バックアップは無効です。
4. 次回はOSの生体認証画面に成功したときだけトークンが取り出され、サーバーでセッションに交換されます。交換のたびにトークンは新しくなり、古いトークンが再び使われた場合は盗用の疑いとして端末の登録を解除し、監査ログに記録します。
5. 紛失時は、本人がWeb版の「設定」→「多要素認証・ログイン端末」で登録解除するか、テナント管理者が「端末の登録解除」を行います。パスワード再発行でも登録端末は解除されます。テナント管理者は生体認証ログインそのものを無効にできます。

## 開発

```sh
flutter pub get
flutter analyze
flutter test
# アプリ×実サーバーの結合テスト（python3とリポジトリのserver/が必要）
BACCHUS_SERVER_TESTS=1 flutter test test/server_contract_test.dart
```

Flutter 3.47以降（Dart 3.13以降）。開発用サーバーへは`http://10.0.2.2:8000`（Androidエミュレーター）や`http://localhost:8000`（iOSシミュレーター）で接続できます。それ以外のサーバーはHTTPSが必須です。

| ファイル | 責任 |
|---|---|
| `lib/src/api.dart` | サーバーAPI（JSON、Bearerトークン、エラー） |
| `lib/src/session.dart` | ログイン・多要素認証・SSO・端末登録・生体認証ログイン |
| `lib/src/vault.dart` | サーバーURL・端末トークンの保存（生体認証必須の保護領域） |
| `lib/src/inventory.dart` | 在庫・消費ルール（Web版・サーバーと同じ） |
| `lib/src/screens/` | ログイン・生体認証・在庫・登録の画面 |

## ビルドと配布

この開発環境ではAndroid SDKを取得できなかったため、ビルドは未確認です。以下を手元のPCまたはCIで実行してください。

### Android

```sh
flutter build apk --release        # 動作確認用
flutter build appbundle --release  # Google Play用
```

- 最小バージョンはAndroid 9（API 28）です。
- リリース署名の鍵（keystore）を作成し、`android/key.properties`と`android/app/build.gradle.kts`の`signingConfigs`を設定します（現在はデバッグ署名）。手順：https://docs.flutter.dev/deployment/android
- アプリIDは`jp.bacchus.bacchus_mobile`です。公開前に組織のドメインに合わせて変更してください。

### iOS（macOSとXcodeが必要）

```sh
cd ios && pod install && cd ..
flutter build ipa --release
```

- Xcodeで`Runner`のSigning & Capabilitiesにチーム（Apple Developer Program）を設定します。
- Face ID・カメラ・写真の利用目的は`ios/Runner/Info.plist`に記載済みです。
- App Store Connectでアプリを登録し、TestFlightで確認してから審査に提出します。手順：https://docs.flutter.dev/deployment/ios

### SSOのリダイレクト

サーバーはアプリからのSSOの結果を`bacchus://sso?code=...`に返します。アプリ側は`android/app/src/main/AndroidManifest.xml`の`CallbackActivity`と`lib/src/session.dart`の`appScheme`、サーバー側は`BACCHUS_APP_SCHEME`で同じスキームを設定します。他のアプリと重ならない名前（例：`jp.example.bacchus`）への変更を推奨します。IDプロバイダーにはWeb版と同じリダイレクトURI（`https://<サーバー>/api/auth/sso/callback`）を登録します。
