import 'dart:io' show Platform;

import 'package:flutter/material.dart';

import '../api.dart';
import '../inventory.dart';
import '../session.dart';
import 'common.dart';
import 'register_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.auth});

  final AuthController auth;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  InventoryState _state = InventoryState.empty;
  String _query = '';
  String? _fridge;
  bool _showEmpty = false;
  bool _loading = true;

  ApiClient get _api => widget.auth.api!;

  @override
  void initState() {
    super.initState();
    _refresh();
    WidgetsBinding.instance.addPostFrameCallback((_) => _offerBiometrics());
  }

  /// Every server call goes through here: a 401 means the session is over.
  Future<void> _call(Future<void> Function() action) async {
    try {
      await action();
    } on ApiException catch (error) {
      if (error.unauthorized) {
        widget.auth.expired();
        return;
      }
      if (mounted) showMessage(context, error.message);
      // A conflict means another phone changed the bottle; show the latest state.
      if (error.status == 409) await _load();
    }
  }

  Future<void> _load() async {
    final data = await _api.get('/api/state') as Map<String, dynamic>;
    if (!mounted) return;
    setState(() {
      _state = InventoryState.fromJson(data);
      if (_fridge != null && !_state.fridges.any((f) => f.id == _fridge)) _fridge = null;
      _loading = false;
    });
  }

  Future<void> _refresh() => _call(_load);

  Future<void> _execute(String type, Map<String, dynamic> payload, String done) => _call(() async {
        final data = await _api.post('/api/commands', command(type, payload)) as Map<String, dynamic>;
        if (!mounted) return;
        setState(() => _state = InventoryState.fromJson(data));
        showMessage(context, done);
      });

  Future<void> _offerBiometrics() async {
    final auth = widget.auth;
    if (!auth.shouldOfferBiometrics || !mounted) return;
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('生体認証でログインしますか？'),
        content: const Text('次回から指紋・顔認証でログインできます。この端末に保存する情報は生体認証でしか取り出せません。紛失時はWeb版の「多要素認証・ログイン端末」から登録を解除できます。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('今はしない')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('登録する')),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    final platform = Platform.isIOS ? 'ios' : Platform.isAndroid ? 'android' : 'other';
    final name = '${auth.session!.user}の${Platform.isIOS ? 'iPhone' : Platform.isAndroid ? 'Android' : '端末'}';
    if (await attempt(context, () => auth.registerDevice(name, platform)) && mounted) {
      showMessage(context, '生体認証でのログインを登録しました');
    }
  }

  Future<void> _move(Bottle bottle) async {
    var fridgeId = bottle.fridgeId;
    final shelf = TextEditingController(text: bottle.shelf);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setLocal) => AlertDialog(
          title: Text('${bottle.name}を移動'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            DropdownButtonFormField<String>(
              initialValue: _state.fridges.any((f) => f.id == fridgeId) ? fridgeId : null,
              decoration: const InputDecoration(labelText: '移動先'),
              items: [for (final f in _state.fridges) DropdownMenuItem(value: f.id, child: Text(f.name))],
              onChanged: (v) => setLocal(() => fridgeId = v ?? fridgeId),
            ),
            TextField(controller: shelf, decoration: const InputDecoration(labelText: '棚・区画'), maxLength: 80),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('キャンセル')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('移動')),
          ],
        ),
      ),
    );
    if (ok == true) {
      await _execute('move', {'bottleId': bottle.id, 'version': bottle.version, 'fridgeId': fridgeId, 'shelf': shelf.text}, '移動しました');
    }
    shelf.dispose();
  }

  Future<void> _settings() async {
    final auth = widget.auth;
    await showModalBottomSheet<void>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.person),
            title: Text(auth.session!.user),
            subtitle: Text('${auth.session!.email}・${auth.session!.tenantName}'),
          ),
          if (auth.deviceRegistered)
            ListTile(
              leading: const Icon(Icons.fingerprint),
              title: const Text('生体認証ログインを解除'),
              onTap: () async {
                Navigator.pop(context);
                await auth.forgetDevice(onServer: true);
              },
            )
          else if (auth.canUseBiometrics)
            ListTile(
              leading: const Icon(Icons.fingerprint),
              title: const Text('生体認証ログインを登録'),
              onTap: () {
                Navigator.pop(context);
                _offerBiometrics();
              },
            ),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('ログアウト'),
            subtitle: auth.deviceRegistered ? const Text('次回は生体認証でログインできます') : null,
            onTap: () {
              Navigator.pop(context);
              auth.logout();
            },
          ),
          const ListTile(
            leading: Icon(Icons.info_outline),
            title: Text('冷蔵庫・利用者・AIの管理はWeb版で行います', style: TextStyle(fontSize: 13)),
          ),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bottles = _state.filter(query: _query, fridgeId: _fridge, showEmpty: _showEmpty);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.auth.session!.tenantName.isEmpty ? 'BACCHUS' : widget.auth.session!.tenantName),
          actions: [
            IconButton(onPressed: _refresh, tooltip: '更新', icon: const Icon(Icons.refresh)),
            IconButton(onPressed: _settings, tooltip: '設定', icon: const Icon(Icons.settings)),
          ],
          bottom: TabBar(tabs: [Tab(text: '在庫 ${_state.bottles.where((b) => !b.empty).length}本'), const Tab(text: '履歴')]),
        ),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: () async {
            if (_state.fridges.isEmpty) {
              showMessage(context, widget.auth.session!.tenantAdmin ? '先にWeb版で冷蔵庫を追加してください' : '冷蔵庫がありません。テナント管理者に追加を依頼してください');
              return;
            }
            final data = await Navigator.push<Map<String, dynamic>>(
              context,
              MaterialPageRoute(builder: (_) => RegisterScreen(auth: widget.auth, fridges: _state.fridges)),
            );
            if (data != null && mounted) setState(() => _state = InventoryState.fromJson(data));
          },
          icon: const Icon(Icons.photo_camera),
          label: const Text('撮影して登録'),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : TabBarView(children: [
                RefreshIndicator(
                  onRefresh: _refresh,
                  child: ListView(padding: const EdgeInsets.fromLTRB(12, 12, 12, 96), children: [
                    TextField(
                      decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: '銘柄・酒蔵で探す'),
                      onChanged: (v) => setState(() => _query = v),
                    ),
                    Row(children: [
                      Expanded(
                        child: DropdownButton<String?>(
                          isExpanded: true,
                          value: _fridge,
                          items: [
                            const DropdownMenuItem(value: null, child: Text('すべての冷蔵庫')),
                            for (final f in _state.fridges) DropdownMenuItem(value: f.id, child: Text(f.name)),
                          ],
                          onChanged: (v) => setState(() => _fridge = v),
                        ),
                      ),
                      const Text('空瓶'),
                      Switch(value: _showEmpty, onChanged: (v) => setState(() => _showEmpty = v)),
                    ]),
                    if (bottles.isEmpty)
                      const Padding(padding: EdgeInsets.all(32), child: Center(child: Text('該当する在庫はありません'))),
                    for (final b in bottles) _BottleCard(bottle: b, location: '${_state.fridgeName(b.fridgeId)}${b.shelf.isEmpty ? '' : ' / ${b.shelf}'}',
                        onConsume: () => _execute(b.onlyRemainder ? 'finish' : 'consume', {'bottleId': b.id, 'version': b.version}, '記録しました'),
                        onMove: () => _move(b)),
                  ]),
                ),
                RefreshIndicator(
                  onRefresh: _refresh,
                  child: ListView(children: [
                    for (final e in _state.events.reversed.take(200))
                      ListTile(
                        dense: true,
                        title: Text('${e.label}　${e.name}${e.amount != null ? '・${e.amount}ml' : ''}'),
                        subtitle: Text('${MaterialLocalizations.of(context).formatShortDate(e.at)} ${TimeOfDay.fromDateTime(e.at).format(context)}・${e.actor}'),
                      ),
                  ]),
                ),
              ]),
      ),
    );
  }
}

class _BottleCard extends StatelessWidget {
  const _BottleCard({required this.bottle, required this.location, required this.onConsume, required this.onMove});

  final Bottle bottle;
  final String location;
  final VoidCallback onConsume;
  final VoidCallback onMove;

  @override
  Widget build(BuildContext context) {
    final photo = bottle.photoBytes;
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: photo != null
                  ? Image.memory(photo, width: 64, height: 84, fit: BoxFit.cover, semanticLabel: '${bottle.name}のラベル')
                  : Container(width: 64, height: 84, color: const Color(0xFFF6EFDF), alignment: Alignment.center, child: const Text('🍶', style: TextStyle(fontSize: 28))),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${bottle.volume} ml・${bottle.openedAt != null ? '開封済み' : '未開封'}', style: Theme.of(context).textTheme.labelSmall),
                Text(bottle.name, style: Theme.of(context).textTheme.titleMedium),
                Text(bottle.brewery.isEmpty ? '酒蔵未登録' : bottle.brewery),
                Text(location, style: Theme.of(context).textTheme.bodySmall),
              ]),
            ),
          ]),
          const SizedBox(height: 8),
          Text('${bottle.remainingLabel}　残量 ${bottle.remaining} ml'),
          LinearProgressIndicator(value: bottle.remaining / bottle.volume, semanticsLabel: '残量'),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(child: FilledButton(onPressed: bottle.empty ? null : onConsume, child: Text(bottle.actionLabel))),
            const SizedBox(width: 8),
            OutlinedButton(onPressed: onMove, child: const Text('移動')),
          ]),
        ]),
      ),
    );
  }
}
