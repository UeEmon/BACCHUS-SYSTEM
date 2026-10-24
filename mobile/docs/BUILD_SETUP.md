# モバイルアプリのビルド環境構築ガイド

BACCHUSアプリ（`mobile/`）をAndroid・iOS向けにビルドし、端末で動かし、ストアへ提出するまでの手順です。

| やりたいこと | 必要なもの |
|---|---|
| Androidのビルド・実機確認 | Windows・macOS・LinuxのPC、Android端末（Android 9以上） |
| iOSのビルド・実機確認 | **Mac**（macOSとXcode）、iPhone（iOS 15以上） |
| Macなしでビルドの確認だけ | GitHubリポジトリ（「5. GitHub Actions」） |
| Google Playへの公開 | Google Play Consoleの開発者アカウント（登録料が必要） |
| App Storeへの公開 | Apple Developer Program（年会費が必要）とMac |

最後に、どの段階でも次のコマンドで環境を確認できます（`mobile/`で実行）。

```sh
dart run tool/check_env.dart
```

問題があれば `[NG]` の行に対処方法を表示します。`flutter doctor -v` も併せて確認してください。

---

## 1. Flutter（共通）

このアプリはFlutter 3.47以上で作っています（開発時は3.47.8）。

**Windows**
1. https://docs.flutter.dev/get-started/install/windows/mobile の手順で、Flutter SDKのzipを `C:\src\flutter` などスペースを含まない場所に展開します（`C:\Program Files` は避ける）。
2. 「環境変数を編集」で、ユーザーの `Path` に `C:\src\flutter\bin` を追加します。
3. 新しいPowerShellで `flutter --version` を確認します。Git for Windowsが必要です。

**macOS**
1. https://docs.flutter.dev/get-started/install/macos/mobile-ios の手順でSDKを `~/development/flutter` に展開します。
2. `~/.zshrc` に `export PATH="$HOME/development/flutter/bin:$PATH"` を追加し、新しいターミナルで `flutter --version` を確認します。
3. Apple Silicon（M1以降）のMacでは `sudo softwareupdate --install-rosetta --agree-to-license` も実行します。

どちらも、続けて次を実行します。

```sh
cd BACCHUS-SYSTEM/mobile
flutter pub get
flutter test          # 11件が成功すればFlutterの準備は完了
```

## 2. Android

### 2-1. Android Studio と SDK

1. https://developer.android.com/studio から Android Studio を入れます（JDK 21が同梱されるので、別途Javaは不要です）。
2. 初回起動のウィザードで「Standard」を選び、Android SDK を入れます。
3. 「Settings」→「Languages & Frameworks」→「Android SDK」→「SDK Tools」で **Android SDK Command-line Tools** にチェックして適用します。
4. ターミナルで次を実行し、表示されるライセンスにすべて `y` で同意します。

   ```sh
   flutter doctor --android-licenses
   ```

5. `flutter doctor` の Android toolchain が ✓ になれば完了です。ビルドに必要なAndroidプラットフォームとビルドツールは、初回ビルド時にGradleが自動でダウンロードします（数分〜十数分かかります）。

### 2-2. 実機で動かす

1. Android端末の「設定」→「デバイス情報」→「ビルド番号」を7回タップして開発者向けオプションを有効にし、「USBデバッグ」をオンにします。
2. USBで接続し、端末に表示される許可ダイアログで「許可」します。`flutter devices` に端末が出ればOKです。
3. 開発用サーバーに接続する場合は、PCとスマートフォンを同じネットワークにつなぎます。アプリはHTTPSのサーバーにしか接続しないため、実機ではHTTPSで公開したサーバー（READMEの「スマートフォンで使う」）を使います。エミュレーターからはPCのサーバーに `http://10.0.2.2:8000` で接続できます。
4. 実行：

   ```sh
   flutter run --release
   ```

5. 指紋認証は端末の「設定」→「セキュリティ」で指紋を登録してから試します。登録がない端末では、生体認証ログインの案内は表示されません。

### 2-3. リリース署名（Google Play配布用）

署名鍵（アップロード鍵）を一度だけ作ります。**この鍵とパスワードを失うと更新版を出せなくなる**ため、パスワード管理ツールなど安全な場所に保管してください。

```sh
# macOS / Linux
keytool -genkey -v -keystore ~/bacchus-upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
# Windows（Android Studio付属のkeytool）
"C:\Program Files\Android\Android Studio\jbr\bin\keytool" -genkey -v -keystore %USERPROFILE%\bacchus-upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
```

`mobile/android/key.properties` を作ります（Gitには登録されません）。

```properties
storePassword=鍵ストアのパスワード
keyPassword=鍵のパスワード
keyAlias=upload
storeFile=/Users/あなた/bacchus-upload-keystore.jks
```

`storeFile` は絶対パス、または `mobile/android/app/` からの相対パスです。`key.properties` があるとリリースビルドがこの鍵で署名され、ない場合はデバッグ署名（動作確認用）になります。

```sh
flutter build appbundle --release   # Google Play用: build/app/outputs/bundle/release/app-release.aab
flutter build apk --release         # 直接配布・確認用: build/app/outputs/flutter-apk/app-release.apk
```

### 2-4. Google Playへの提出

1. Play Console（https://play.google.com/console）でアプリを作成します。
2. 公開前に、アプリID `jp.bacchus.bacchus_mobile` を自社のドメインに合わせて変更します（`android/app/build.gradle.kts` の `applicationId` と `namespace`、`MainActivity.kt` のパッケージ）。**公開後は変更できません**。
3. 「内部テスト」トラックに `.aab` をアップロードし、テスターの端末で確認してから製品版へ進めます。Play App Signingを使うと、配布用の署名はGoogleが管理します。
4. データセーフティの申告では、メールアドレス・写真（ラベル）をサーバーへ送信すること、生体情報は端末外へ送らないことを記載します。

## 3. iOS（Macが必要）

### 3-1. Xcode

1. App Storeから **Xcode** を入れて一度起動し、追加コンポーネントのインストールと利用規約への同意を済ませます。
2. ターミナルで次を実行します。

   ```sh
   sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
   sudo xcodebuild -runFirstLaunch
   xcodebuild -downloadPlatform iOS   # iOSシミュレーター
   ```

3. `flutter doctor` が CocoaPods を求めた場合は `brew install cocoapods`（Homebrewが必要）で入れます。
4. `flutter doctor` の Xcode が ✓ になれば完了です。

### 3-2. シミュレーター・実機で動かす

```sh
open -a Simulator
flutter run
```

シミュレーターでは「Features」→「Face ID」→「Enrolled」で生体認証を試せます。

実機（iPhone）の場合：

1. Xcodeで `mobile/ios/Runner.xcworkspace` を開きます。
2. 「Runner」→「Signing & Capabilities」で **Team** に自分のApple ID（またはApple Developer Programのチーム）を選びます。Bundle Identifier が他と重なるとエラーになるため、`jp.<組織>.bacchus` など固有の値に変更します。
3. iPhoneをUSBで接続し、iPhoneの「設定」→「プライバシーとセキュリティ」→「デベロッパモード」をオンにします。
4. `flutter run --release` で起動します。無料のApple IDでは7日で起動できなくなるため、継続利用にはApple Developer Programが必要です。

### 3-3. App Storeへの提出

1. Apple Developer Program に登録します。
2. App Store Connect（https://appstoreconnect.apple.com）でアプリを作成し、Bundle Identifier を登録します。
3. ビルドしてアップロードします。

   ```sh
   flutter build ipa --release
   # 生成された build/ios/archive/Runner.xcarchive を Xcode の Organizer で開き「Distribute App」
   # または Transporter アプリで build/ios/ipa/*.ipa をアップロード
   ```

4. TestFlightで社内テストをしてから審査に提出します。審査用に、テスト用テナントのログイン情報（多要素認証なしのメンバー）とサーバーURLを「App Review Information」に記載します。
5. App Privacyの申告では、メールアドレスと写真（ラベル）の収集、Face IDの情報は端末外へ送らないことを記載します。

## 4. アプリとサーバーの設定合わせ

| 項目 | アプリ側 | サーバー側 |
|---|---|---|
| SSOの戻り先 | `android/app/src/main/AndroidManifest.xml` の `CallbackActivity`（`android:scheme`）、`lib/src/session.dart` の `appScheme` | `BACCHUS_APP_SCHEME`（既定 `bacchus`） |
| SSOのリダイレクトURI | — | IDプロバイダーに `https://<サーバー>/api/auth/sso/callback` を登録、`BACCHUS_PUBLIC_URL` を設定 |
| 生体認証ログイン | 端末に指紋・顔を登録 | テナント管理者の「専用アプリでの生体認証ログインを許可する」がオン |

スキームは他のアプリと重ならない名前（例 `jp.example.bacchus`）に変える場合、3か所すべてを同じ値にします。

## 5. GitHub Actions（Macがない場合・自動ビルド）

`.github/workflows/mobile.yml` が、`mobile/` や `server/` を変更してpushするたびに次を実行します（GitHubの「Actions」タブで結果を確認。手動実行も可）。

| ジョブ | 実行環境 | 内容 |
|---|---|---|
| test | Ubuntu | 静的解析、アプリのテスト、実サーバーとの結合テスト |
| android | Ubuntu | APK・AABのビルド（成果物としてダウンロード可） |
| ios | macOS | iOSのビルド（署名なし。コンパイルできることの確認） |

**Androidのリリース署名をCIで行う場合**は、リポジトリの「Settings」→「Secrets and variables」→「Actions」に次の4つを登録します。

| シークレット | 値 |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `base64 -i bacchus-upload-keystore.jks`（Windowsは `certutil -encode` の本文部分）の出力 |
| `ANDROID_STORE_PASSWORD` | 鍵ストアのパスワード |
| `ANDROID_KEY_ALIAS` | `upload` |
| `ANDROID_KEY_PASSWORD` | 鍵のパスワード |

**iOSの署名付きビルド・App Storeへのアップロード**をCIで行うには、配布用証明書・プロビジョニングプロファイル（またはApp Store Connect APIキー）をシークレットに登録する追加設定が必要です。最初はMacのXcodeから手動でアップロードし、運用が固まってからfastlane等で自動化することを推奨します。

macOSの実行環境はGitHubの無料枠の消費が大きい（Linuxの約10倍）ため、私用リポジトリでは利用時間に注意してください。

## 6. よくあるトラブル

| 症状 | 対処 |
|---|---|
| `flutter doctor` で Android license status unknown | `flutter doctor --android-licenses` を実行してすべて同意 |
| Gradleのビルドで `Could not resolve ...` / HTTP 429 | 一時的な通信制限です。時間をおいて再実行するか、社内プロキシ環境ではプロキシ設定（`~/.gradle/gradle.properties` の `systemProp.https.proxyHost` 等）を確認 |
| `Unsupported class file major version` | JDKが新しすぎるか古すぎます。Android Studio付属のJDKを使う：`flutter config --jdk-dir "<Android Studio>/jbr"` |
| Androidで生体認証ログインの案内が出ない | 端末に指紋が登録されているか、Android 9以上か、クラス3（Strong）の生体認証に対応しているかを確認 |
| iOSで `No signing certificate` | Xcodeの「Signing & Capabilities」でTeamを選択 |
| iOSで `CocoaPods not installed` | `brew install cocoapods` の後 `flutter clean && flutter pub get` |
| SSOでブラウザから戻ってこない | サーバーの `BACCHUS_APP_SCHEME` とアプリのスキームが一致しているか、IDプロバイダーにリダイレクトURIを登録したかを確認 |
| 「生体認証の登録が見つかりません」 | 端末の指紋・顔を登録し直すと保護された鍵は無効になります（安全のための仕様）。パスワードまたはSSOでログインし直して再登録 |

## 7. 既知の注意点

- **flutter_web_auth_2 と Kotlin**：Androidのビルド時に「Your app uses the following plugins that apply Kotlin Gradle Plugin (KGP): flutter_web_auth_2」という警告が出ます。現在のFlutter（3.47）ではビルドできますが、将来のFlutterでは失敗する予定です。Flutterを更新する前に、SSO用プラグイン flutter_web_auth_2 が対応版（Built-in Kotlin対応）を出しているか確認してください。対応版が出ない場合は、SSOの画面表示を url_launcher と app_links の組み合わせに置き換えます（変更箇所は `lib/src/session.dart` の `systemBrowser` だけです）。
- **このリポジトリでの確認状況**：Androidのリリースビルド（APK）は開発環境で成功し、署名設定（key.properties）での署名も確認済みです。iOSはMacまたはGitHub Actionsでの確認が必要です。

