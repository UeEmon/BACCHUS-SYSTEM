import 'package:flutter/material.dart';

import '../session.dart';
import 'common.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.auth});

  final AuthController auth;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _form = GlobalKey<FormState>();
  late final _server = TextEditingController(text: widget.auth.savedServer);
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _code = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    for (final c in [_server, _email, _password, _code]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    await attempt(context, action);
    if (mounted) setState(() => _busy = false);
  }

  void _login() {
    if (!_form.currentState!.validate()) return;
    _run(() async {
      await widget.auth.login(_server.text, _email.text, _password.text);
      _password.clear();
    });
  }

  void _sso() {
    if (_server.text.trim().isEmpty || _email.text.trim().isEmpty) {
      showMessage(context, 'サーバーとメールアドレスを入力してください');
      return;
    }
    _run(() => widget.auth.loginWithSso(_server.text, _email.text));
  }

  @override
  Widget build(BuildContext context) {
    final mfa = widget.auth.awaitingMfa;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: AutofillGroup(
                child: Form(
                  key: _form,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    const Brand(),
                    const SizedBox(height: 32),
                    if (!mfa) ...[
                      TextFormField(
                        controller: _server,
                        decoration: const InputDecoration(labelText: 'サーバー', hintText: 'sake.example.com'),
                        keyboardType: TextInputType.url,
                        autocorrect: false,
                        validator: (v) => (v ?? '').trim().isEmpty ? 'サーバーのURLを入力してください' : null,
                      ),
                      TextFormField(
                        controller: _email,
                        decoration: const InputDecoration(labelText: 'メールアドレス'),
                        keyboardType: TextInputType.emailAddress,
                        autofillHints: const [AutofillHints.username, AutofillHints.email],
                        autocorrect: false,
                        validator: (v) => (v ?? '').contains('@') ? null : 'メールアドレスを入力してください',
                      ),
                      TextFormField(
                        controller: _password,
                        decoration: const InputDecoration(labelText: 'パスワード'),
                        obscureText: true,
                        autofillHints: const [AutofillHints.password],
                        onFieldSubmitted: (_) => _login(),
                        validator: (v) => (v ?? '').isEmpty ? 'パスワードを入力してください' : null,
                      ),
                      const SizedBox(height: 24),
                      FilledButton(onPressed: _busy ? null : _login, child: const Text('ログイン')),
                      const SizedBox(height: 8),
                      OutlinedButton(onPressed: _busy ? null : _sso, child: const Text('SSOでログイン')),
                      const SizedBox(height: 16),
                      const Text('SSOはサーバーとメールアドレスだけ入力して押してください。初回のパスワード変更と多要素認証の設定はWeb版で行います。', style: TextStyle(fontSize: 12)),
                    ] else ...[
                      const Text('認証アプリに表示された6桁のコード、または回復コードを入力してください。'),
                      TextField(
                        controller: _code,
                        decoration: const InputDecoration(labelText: '確認コード'),
                        keyboardType: TextInputType.number,
                        autofillHints: const [AutofillHints.oneTimeCode],
                        autofocus: true,
                        onSubmitted: (_) => _run(() => widget.auth.verifyMfa(_code.text)),
                      ),
                      const SizedBox(height: 24),
                      FilledButton(onPressed: _busy ? null : () => _run(() => widget.auth.verifyMfa(_code.text)), child: const Text('確認してログイン')),
                      TextButton(onPressed: _busy ? null : widget.auth.cancelMfa, child: const Text('最初からやり直す')),
                    ],
                    if (_busy) const Padding(padding: EdgeInsets.only(top: 16), child: Center(child: CircularProgressIndicator())),
                  ]),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
