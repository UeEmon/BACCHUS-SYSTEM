// Checks this machine for what building the BACCHUS app needs, on top of `flutter doctor`.
// Run from mobile/:  dart run tool/check_env.dart
import 'dart:io';

int failures = 0;

void ok(String text) => stdout.writeln('  [OK]   $text');
void warn(String text) => stdout.writeln('  [注意] $text');
void fail(String text) {
  failures++;
  stdout.writeln('  [NG]   $text');
}

Future<String?> run(String command, List<String> args) async {
  try {
    final result = await Process.run(command, args, runInShell: Platform.isWindows);
    return result.exitCode == 0 ? '${result.stdout}${result.stderr}'.trim() : null;
  } on ProcessException {
    return null;
  }
}

List<int> version(String text) =>
    RegExp(r'(\d+)\.(\d+)(?:\.(\d+))?').firstMatch(text)?.groups([1, 2, 3]).map((g) => int.parse(g ?? '0')).toList() ?? [0, 0, 0];

bool atLeast(List<int> have, List<int> need) {
  for (var i = 0; i < 3; i++) {
    if (have[i] != need[i]) return have[i] > need[i];
  }
  return true;
}

Future<void> main() async {
  stdout.writeln('BACCHUS モバイルアプリのビルド環境チェック（${Platform.operatingSystem}）\n');

  stdout.writeln('共通');
  final flutter = await run('flutter', ['--version']);
  if (flutter == null) {
    fail('Flutterが見つかりません。docs/BUILD_SETUP.md の「1. Flutter」を参照してください');
  } else if (!atLeast(version(flutter), [3, 47, 0])) {
    fail('Flutter 3.47以上が必要です（現在 ${version(flutter).join('.')}）。flutter upgrade を実行してください');
  } else {
    ok('Flutter ${version(flutter).join('.')}');
  }
  if (!File('pubspec.yaml').existsSync()) fail('mobile/ フォルダで実行してください');

  stdout.writeln('\nAndroid');
  final java = await run('java', ['-version']);
  // Match the version line itself; JVM banners such as JAVA_TOOL_OPTIONS can hold other numbers.
  final javaMatch = java == null ? null : RegExp(r'version "(\d+)(?:\.(\d+))?').firstMatch(java);
  final javaVersion = javaMatch == null ? null : [int.parse(javaMatch.group(1)!), int.parse(javaMatch.group(2) ?? '0'), 0];
  if (javaVersion == null) {
    fail('Javaが見つかりません。Android Studio付属のJDK（JDK 21）を使うか、JDK 17〜21を入れてください');
  } else if (javaVersion[0] < 17) {
    fail('JDK 17以上が必要です（現在 ${javaVersion[0]}）');
  } else {
    ok('JDK ${javaVersion[0]}');
  }
  final sdk = Platform.environment['ANDROID_HOME'] ?? Platform.environment['ANDROID_SDK_ROOT'];
  final doctor = await run('flutter', ['doctor', '-v']) ?? '';
  if (doctor.contains('Android SDK at') || (sdk != null && Directory(sdk).existsSync())) {
    ok('Android SDK${sdk != null ? '（$sdk）' : ''}');
  } else {
    fail('Android SDKが見つかりません。Android Studioを入れて「SDK Manager」からSDKを入れてください');
  }
  if (doctor.contains('Android license status unknown') || doctor.contains('Some Android licenses not accepted')) {
    fail('Android SDKのライセンスが未承諾です。flutter doctor --android-licenses を実行してください');
  }
  if (File('android/key.properties').existsSync()) {
    final props = File('android/key.properties').readAsStringSync();
    final store = RegExp(r'^storeFile=(.+)$', multiLine: true).firstMatch(props)?.group(1)?.trim();
    if (store == null) {
      fail('android/key.properties に storeFile がありません');
    } else if (!File(File(store).isAbsolute ? store : 'android/app/$store').existsSync()) {
      fail('署名鍵ファイル $store が見つかりません（相対パスは android/app/ 基準）');
    } else {
      ok('リリース署名の設定（android/key.properties）');
    }
  } else {
    warn('android/key.properties がないため、リリースビルドはデバッグ署名になります（動作確認用。ストア配布には署名が必要）');
  }

  stdout.writeln('\niOS');
  if (!Platform.isMacOS) {
    warn('iOSのビルドにはmacOSが必要です。Macがない場合はGitHub Actions（docs/BUILD_SETUP.md の「5」）を使います');
  } else {
    final xcode = await run('xcodebuild', ['-version']);
    if (xcode == null) {
      fail('Xcodeが見つかりません。App StoreからXcodeを入れ、sudo xcode-select -s /Applications/Xcode.app を実行してください');
    } else {
      ok(xcode.split('\n').first);
    }
    if (doctor.contains('CocoaPods not installed')) {
      fail('CocoaPodsが必要です。brew install cocoapods を実行してください');
    }
    if (await run('xcrun', ['simctl', 'list', 'runtimes']) case final runtimes? when !runtimes.contains('iOS')) {
      warn('iOSシミュレーターのランタイムがありません。Xcode → Settings → Components から入れてください');
    }
  }

  stdout.writeln(failures == 0 ? '\n準備できています。' : '\n$failures 件の問題があります。上の [NG] を解決してから再実行してください。');
  exit(failures == 0 ? 0 : 1);
}
