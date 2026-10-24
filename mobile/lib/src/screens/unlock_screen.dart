import 'package:flutter/material.dart';

import '../session.dart';
import 'common.dart';

/// Shown when this phone is registered: one tap brings up the system's
/// fingerprint or face prompt.
class UnlockScreen extends StatefulWidget {
  const UnlockScreen({super.key, required this.auth});

  final AuthController auth;

  @override
  State<UnlockScreen> createState() => _UnlockScreenState();
}

class _UnlockScreenState extends State<UnlockScreen> {
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _unlock());
  }

  Future<void> _unlock() async {
    if (_busy) return;
    setState(() => _busy = true);
    await attempt(context, widget.auth.unlock);
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Brand(),
              const SizedBox(height: 16),
              Text(Uri.tryParse(widget.auth.savedServer)?.host ?? '', style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: 40),
              IconButton.filled(
                iconSize: 56,
                onPressed: _busy ? null : _unlock,
                tooltip: '生体認証でログイン',
                icon: const Icon(Icons.fingerprint),
              ),
              const SizedBox(height: 12),
              const Text('生体認証でログイン'),
              const SizedBox(height: 40),
              TextButton(
                onPressed: _busy ? null : () => widget.auth.forgetDevice(),
                child: const Text('パスワードまたはSSOでログイン（この端末の登録を解除）'),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
