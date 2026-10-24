import 'dart:convert';

import 'package:bacchus_mobile/src/vault.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class MemoryVault implements CredentialVault {
  String? storedServer;
  String? storedDeviceId;
  String? storedToken;
  int tokenReads = 0;

  @override
  Future<String?> server() async => storedServer;

  @override
  Future<void> saveServer(String server) async => storedServer = server;

  @override
  Future<String?> deviceId() async => storedDeviceId;

  @override
  Future<String?> readToken() async {
    tokenReads++;
    return storedToken;
  }

  @override
  Future<void> save(DeviceCredential credential) async {
    storedDeviceId = credential.deviceId;
    storedToken = credential.token;
  }

  @override
  Future<void> clear() async => storedDeviceId = storedToken = null;
}

class FixedBiometrics implements BiometricSupport {
  FixedBiometrics(this.value);

  final bool value;

  @override
  Future<bool> available() async => value;
}

typedef Route = Object? Function(Map<String, dynamic> body, String? token);

/// A scripted server: each path answers with a value, or [ServerError].
class ServerError {
  const ServerError(this.status, this.message);

  final int status;
  final String message;
}

class FakeServer {
  FakeServer(this.routes);

  final Map<String, Route> routes;
  final List<String> calls = [];

  http.Client get client => MockClient((request) async {
        final path = request.url.path;
        calls.add(path);
        final route = routes[path];
        if (route == null) return http.Response(jsonEncode({'error': 'not found'}), 404);
        final auth = request.headers['Authorization'];
        final body = request.body.isEmpty ? <String, dynamic>{} : jsonDecode(request.body) as Map<String, dynamic>;
        final result = route(body, auth?.replaceFirst('Bearer ', ''));
        if (result is ServerError) {
          return http.Response.bytes(utf8.encode(jsonEncode({'error': result.message})), result.status, headers: {'content-type': 'application/json'});
        }
        return http.Response.bytes(utf8.encode(jsonEncode(result)), 200, headers: {'content-type': 'application/json'});
      });
}

Map<String, dynamic> sessionJson({String role = 'member', bool mustChange = false, bool mfaSetup = false, bool tenant = true}) => {
      'user': '太郎',
      'email': 'taro@example.com',
      'role': role,
      'tenant': tenant ? {'id': 't', 'name': 'A店'} : null,
      'workspace': tenant ? 'w' : null,
      'mustChangePassword': mustChange,
      'mfaSetupRequired': mfaSetup,
      'aiConfigured': true,
    };
