import 'package:flutter/material.dart';

import '../api.dart';

/// Runs an action, shows its error in a snack bar and reports whether it succeeded.
Future<bool> attempt(BuildContext context, Future<void> Function() action) async {
  try {
    await action();
    return true;
  } on ApiException catch (error) {
    if (context.mounted) showMessage(context, error.message);
  } on FormatException catch (error) {
    if (context.mounted) showMessage(context, error.message);
  } on Exception {
    if (context.mounted) showMessage(context, '操作を完了できませんでした。もう一度お試しください');
  }
  return false;
}

void showMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}

class Brand extends StatelessWidget {
  const Brand({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(children: [
      Text('YOUR SAKE COLLECTION', style: theme.textTheme.labelSmall?.copyWith(letterSpacing: 2)),
      Text('BACCHUS', style: theme.textTheme.headlineLarge?.copyWith(fontWeight: FontWeight.bold, color: const Color(0xFF173D37))),
      const Text('日本酒の冷蔵庫'),
    ]);
  }
}
