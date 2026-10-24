import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// An error the server reported, with its HTTP status and Japanese message.
class ApiException implements Exception {
  ApiException(this.status, this.message);

  final int status;
  final String message;

  bool get unauthorized => status == 401;

  @override
  String toString() => message;
}

/// JSON API of the BACCHUS server. The session token is sent as a bearer token,
/// exactly like the web app does.
class ApiClient {
  ApiClient(this.baseUri, {http.Client? client, this.token = ''})
      : _client = client ?? http.Client();

  final Uri baseUri;
  final http.Client _client;
  String token;

  static const _timeout = Duration(seconds: 65);

  /// Accepts what a person types: a host or a URL. HTTPS is required except for
  /// a development server on this machine or the Android emulator's host.
  static Uri parseServer(String input) {
    var text = input.trim();
    if (text.isEmpty) throw const FormatException('サーバーのURLを入力してください');
    if (!text.contains('://')) text = 'https://$text';
    final uri = Uri.tryParse(text);
    if (uri == null || uri.host.isEmpty) {
      throw const FormatException('サーバーのURLが正しくありません');
    }
    final local = {'localhost', '127.0.0.1', '10.0.2.2'}.contains(uri.host);
    if (uri.scheme != 'https' && !(uri.scheme == 'http' && local)) {
      throw const FormatException('サーバーのURLは https:// で始めてください');
    }
    return uri.replace(path: '', query: null, fragment: null);
  }

  Future<dynamic> get(String path) => _send('GET', path);

  Future<dynamic> post(String path, [Map<String, dynamic> body = const {}]) =>
      _send('POST', path, body);

  Future<dynamic> _send(String method, String path, [Map<String, dynamic>? body]) async {
    final request = http.Request(method, baseUri.resolve(path))
      ..headers['Content-Type'] = 'application/json'
      ..headers['Accept'] = 'application/json';
    if (token.isNotEmpty) request.headers['Authorization'] = 'Bearer $token';
    if (body != null) request.body = jsonEncode(body);
    final http.Response response;
    try {
      response = await http.Response.fromStream(await _client.send(request).timeout(_timeout));
    } on TimeoutException {
      throw ApiException(0, 'サーバーの応答がありません。通信状態を確認してください');
    } on http.ClientException {
      throw ApiException(0, 'サーバーに接続できません。URLと通信状態を確認してください');
    }
    dynamic data;
    try {
      data = jsonDecode(utf8.decode(response.bodyBytes));
    } on FormatException {
      throw ApiException(response.statusCode, 'サーバーの応答を読み取れません（HTTP ${response.statusCode}）');
    }
    if (response.statusCode >= 400) {
      final message = data is Map && data['error'] is String ? data['error'] as String : 'エラーが発生しました（HTTP ${response.statusCode}）';
      throw ApiException(response.statusCode, message);
    }
    return data;
  }
}
