import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api.dart';
import '../inventory.dart';
import '../session.dart';
import 'common.dart';

/// Photograph a label, optionally let the tenant's AI read it, check and register.
/// Returns the new inventory state.
class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, required this.auth, required this.fridges, this.picker});

  final AuthController auth;
  final List<Fridge> fridges;
  final ImagePicker? picker;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _brewery = TextEditingController();
  final _volume = TextEditingController(text: '720');
  final _count = TextEditingController(text: '1');
  final _shelf = TextEditingController();
  final _category = TextEditingController();
  final _productionDate = TextEditingController();
  late String _fridgeId = widget.fridges.first.id;
  String _photo = '';
  String _status = '';
  bool _busy = false;

  @override
  void dispose() {
    for (final c in [_name, _brewery, _volume, _count, _shelf, _category, _productionDate]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _pick(ImageSource source) async {
    // The server accepts JPEG up to about 1.2 MB; 1280 px at 80% quality stays well below.
    final file = await (widget.picker ?? ImagePicker()).pickImage(source: source, maxWidth: 1280, maxHeight: 1280, imageQuality: 80);
    if (file == null) return;
    final bytes = await file.readAsBytes();
    if (bytes.length > 1150000) {
      if (mounted) showMessage(context, '写真が大きすぎます。近づいて撮り直してください');
      return;
    }
    setState(() {
      _photo = 'data:image/jpeg;base64,${base64Encode(bytes)}';
      _status = '写真を確認してAIで読み取るか、手入力してください。';
    });
  }

  Future<void> _recognize() async {
    setState(() {
      _busy = true;
      _status = 'ラベルを読み取っています…';
    });
    try {
      final r = await widget.auth.api!.post('/api/recognize', {'photo': _photo}) as Map<String, dynamic>;
      void fill(TextEditingController c, Object? v) {
        if (v is String && v.isNotEmpty) c.text = v;
      }
      fill(_name, r['name']);
      fill(_brewery, r['brewery']);
      fill(_category, r['category']);
      fill(_productionDate, r['productionDate']);
      _volume.text = r['volume'] is int ? '${r['volume']}' : '';
      setState(() => _status = '読み取り完了（${r['provider']}・${r['model']}）。内容を確認してください。${r['notes'] ?? ''}');
    } on ApiException catch (error) {
      if (error.unauthorized) widget.auth.expired();
      setState(() => _status = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final count = int.parse(_count.text);
    setState(() => _busy = true);
    final ok = await attempt(context, () async {
      final data = await widget.auth.api!.post('/api/commands', command('receive', {
        'name': _name.text,
        'brewery': _brewery.text,
        'volume': int.parse(_volume.text),
        'count': count,
        'ids': List.generate(count, (_) => newId()),
        'fridgeId': _fridgeId,
        'shelf': _shelf.text,
        'category': _category.text,
        'productionDate': _productionDate.text,
        'photo': _photo,
      })) as Map<String, dynamic>;
      if (mounted) Navigator.pop(context, data);
    });
    if (!ok && mounted) setState(() => _busy = false);
  }

  String? _range(String? value, int min, int max) {
    final n = int.tryParse(value ?? '');
    return n == null || n < min || n > max ? '$min〜$maxの整数で入力してください' : null;
  }

  @override
  Widget build(BuildContext context) {
    final photo = _photo.isEmpty ? null : base64Decode(_photo.split(',').last);
    return Scaffold(
      appBar: AppBar(title: const Text('日本酒を登録')),
      body: Form(
        key: _form,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          Row(children: [
            Expanded(child: FilledButton.tonalIcon(onPressed: _busy ? null : () => _pick(ImageSource.camera), icon: const Icon(Icons.photo_camera), label: const Text('撮影'))),
            const SizedBox(width: 8),
            Expanded(child: OutlinedButton.icon(onPressed: _busy ? null : () => _pick(ImageSource.gallery), icon: const Icon(Icons.photo), label: const Text('写真を選ぶ'))),
          ]),
          if (photo != null) Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: Image.memory(photo, height: 220, fit: BoxFit.contain)),
          if (photo != null)
            OutlinedButton(onPressed: _busy || !widget.auth.session!.aiConfigured ? null : _recognize, child: Text(widget.auth.session!.aiConfigured ? 'AIでラベルを読み取る' : 'AIは未設定です（手入力してください）')),
          if (_status.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_status)),
          const Text('AI解析時のみ、写真をサーバー経由でテナントが設定したAIサービスへ送信します。結果は必ず確認してください。', style: TextStyle(fontSize: 12)),
          TextFormField(controller: _name, decoration: const InputDecoration(labelText: '銘柄（必須）'), maxLength: 200, validator: (v) => (v ?? '').trim().isEmpty ? '銘柄を入力してください' : null),
          TextFormField(controller: _brewery, decoration: const InputDecoration(labelText: '酒蔵'), maxLength: 200),
          Row(children: [
            Expanded(child: TextFormField(controller: _volume, decoration: const InputDecoration(labelText: '容量（ml）'), keyboardType: TextInputType.number, validator: (v) => _range(v, 1, 10000))),
            const SizedBox(width: 12),
            Expanded(child: TextFormField(controller: _count, decoration: const InputDecoration(labelText: '入荷本数'), keyboardType: TextInputType.number, validator: (v) => _range(v, 1, 100))),
          ]),
          DropdownButtonFormField<String>(
            initialValue: _fridgeId,
            decoration: const InputDecoration(labelText: '冷蔵庫'),
            items: [for (final f in widget.fridges) DropdownMenuItem(value: f.id, child: Text(f.name))],
            onChanged: (v) => setState(() => _fridgeId = v ?? _fridgeId),
          ),
          TextFormField(controller: _shelf, decoration: const InputDecoration(labelText: '棚・区画（任意）'), maxLength: 80),
          TextFormField(controller: _category, decoration: const InputDecoration(labelText: '特定名称'), maxLength: 200),
          TextFormField(controller: _productionDate, decoration: const InputDecoration(labelText: '製造年月（ラベルの表記のまま）'), maxLength: 40),
          const SizedBox(height: 16),
          FilledButton(onPressed: _busy ? null : _submit, child: const Text('確認して登録')),
        ]),
      ),
    );
  }
}
