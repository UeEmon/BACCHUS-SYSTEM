import 'package:flutter/foundation.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:http/http.dart' as http;

import 'api.dart';
import 'vault.dart';

/// The custom URL scheme the server sends SSO results to (BACCHUS_APP_SCHEME).
const appScheme = 'bacchus';

typedef SsoBrowser = Future<String> Function(String url, String callbackScheme);

Future<String> systemBrowser(String url, String callbackScheme) =>
    FlutterWebAuth2.authenticate(url: url, callbackUrlScheme: callbackScheme);

class SessionInfo {
  const SessionInfo({required this.user, required this.email, required this.role, required this.tenantName, required this.aiConfigured});

  factory SessionInfo.fromJson(Map<String, dynamic> json) => SessionInfo(
        user: json['user'] as String,
        email: json['email'] as String? ?? '',
        role: json['role'] as String,
        tenantName: (json['tenant'] as Map<String, dynamic>?)?['name'] as String? ?? '',
        aiConfigured: json['aiConfigured'] as bool? ?? false,
      );

  final String user;
  final String email;
  final String role;
  final String tenantName;
  final bool aiConfigured;

  bool get tenantAdmin => role == 'tenant_admin';
}

/// Sign-in state for the whole app.
///
/// Passwords go to the server once and are never stored. After a sign-in by
/// password (with MFA when enabled) or SSO, the phone may be registered: the
/// server issues a device token that the app keeps behind the biometric check
/// and exchanges for a session next time. Each exchange rotates the token, and
/// the server unregisters a device whose old token is replayed.
class AuthController extends ChangeNotifier {
  AuthController({required this.vault, required this.biometrics, this.client, this.browser = systemBrowser});

  final CredentialVault vault;
  final BiometricSupport biometrics;
  final SsoBrowser browser;
  /// HTTP client to use; tests pass a fake one.
  final http.Client? client;

  ApiClient? api;
  SessionInfo? session;
  String? _challenge;
  String savedServer = '';
  bool deviceRegistered = false;
  bool canUseBiometrics = false;
  bool ready = false;

  bool get signedIn => session != null;
  bool get awaitingMfa => _challenge != null;

  Future<void> load() async {
    savedServer = await vault.server() ?? '';
    deviceRegistered = savedServer.isNotEmpty && await vault.deviceId() != null;
    canUseBiometrics = await biometrics.available();
    ready = true;
    notifyListeners();
  }

  ApiClient _connect(String server) {
    final uri = ApiClient.parseServer(server);
    return api = ApiClient(uri, client: client);
  }

  Future<void> login(String server, String email, String password) async {
    final client = _connect(server);
    final result = await client.post('/api/auth/login', {'email': email.trim(), 'password': password}) as Map<String, dynamic>;
    await vault.saveServer(client.baseUri.toString());
    savedServer = client.baseUri.toString();
    if (result['mfaRequired'] == true) {
      _challenge = result['challenge'] as String;
      notifyListeners();
      return;
    }
    await _begin(result['session'] as String);
  }

  Future<void> verifyMfa(String code) async {
    final result = await api!.post('/api/auth/mfa/verify', {'challenge': _challenge, 'code': code.trim()}) as Map<String, dynamic>;
    _challenge = null;
    await _begin(result['session'] as String);
  }

  void cancelMfa() {
    _challenge = null;
    notifyListeners();
  }

  /// SSO through the system browser (ASWebAuthenticationSession / Custom Tabs);
  /// the server returns to bacchus://sso with a one-time code.
  Future<void> loginWithSso(String server, String email) async {
    final client = _connect(server);
    final start = await client.post('/api/auth/sso/start', {'email': email.trim(), 'client': 'app'}) as Map<String, dynamic>;
    final result = Uri.parse(await browser(start['url'] as String, appScheme));
    final error = result.queryParameters['error'];
    if (error != null) throw ApiException(401, error);
    final code = result.queryParameters['code'];
    if (code == null) throw ApiException(401, 'SSOでログインできませんでした');
    await vault.saveServer(client.baseUri.toString());
    savedServer = client.baseUri.toString();
    final exchanged = await client.post('/api/auth/exchange', {'code': code}) as Map<String, dynamic>;
    await _begin(exchanged['session'] as String);
  }

  /// Signs in with the registered device: the system shows the biometric prompt
  /// when the token is read.
  Future<void> unlock() async {
    final client = _connect(savedServer);
    final deviceId = await vault.deviceId();
    final token = await vault.readToken();
    if (deviceId == null || token == null) {
      await forgetDevice();
      throw ApiException(401, '生体認証の登録が見つかりません。パスワードまたはSSOでログインしてください');
    }
    try {
      final result = await client.post('/api/auth/device-login', {'deviceId': deviceId, 'deviceToken': token}) as Map<String, dynamic>;
      await vault.save(DeviceCredential(deviceId: deviceId, token: result['deviceToken'] as String));
      await _begin(result['session'] as String);
    } on ApiException catch (error) {
      if (error.unauthorized) await forgetDevice();
      rethrow;
    }
  }

  Future<void> _begin(String token) async {
    api!.token = token;
    final info = await api!.get('/api/session') as Map<String, dynamic>;
    // First-time steps are done in the web app, where they can be shown in full.
    if (info['mustChangePassword'] == true || info['mfaSetupRequired'] == true) {
      await _endSession();
      throw ApiException(403, info['mustChangePassword'] == true
          ? '初回ログインのため、Web版でパスワードを変更してからアプリをご利用ください'
          : '多要素認証が必須です。Web版の「設定」→「多要素認証・ログイン端末」で設定してからアプリをご利用ください');
    }
    if (info['workspace'] == null) {
      await _endSession();
      throw ApiException(403, '全体管理者の操作はWeb版をご利用ください');
    }
    session = SessionInfo.fromJson(info);
    notifyListeners();
  }

  /// Offered after a full sign-in, when the phone has biometrics.
  bool get shouldOfferBiometrics => signedIn && canUseBiometrics && !deviceRegistered;

  Future<void> registerDevice(String name, String platform) async {
    final result = await api!.post('/api/auth/devices', {'name': name, 'platform': platform}) as Map<String, dynamic>;
    await vault.save(DeviceCredential(deviceId: result['deviceId'] as String, token: result['deviceToken'] as String));
    deviceRegistered = true;
    notifyListeners();
  }

  Future<void> forgetDevice({bool onServer = false}) async {
    final id = await vault.deviceId();
    if (onServer && id != null && signedIn) {
      try {
        await api!.post('/api/auth/devices/revoke', {'deviceId': id});
      } on ApiException {
        // Unregistering here still protects this phone; the server copy can be removed from the web app.
      }
    }
    await vault.clear();
    deviceRegistered = false;
    notifyListeners();
  }

  Future<void> logout() async {
    await _endSession();
    notifyListeners();
  }

  /// Called when the server answers 401: the session ended or was revoked.
  void expired() {
    session = null;
    api?.token = '';
    notifyListeners();
  }

  Future<void> _endSession() async {
    if (api != null && api!.token.isNotEmpty) {
      try {
        await api!.post('/api/auth/logout');
      } on ApiException {
        // The session ends on the server by expiry anyway.
      }
      api!.token = '';
    }
    session = null;
  }
}
