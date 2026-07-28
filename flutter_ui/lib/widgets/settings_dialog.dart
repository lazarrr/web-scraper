import 'package:flutter/material.dart';

/// A dialog that lets the user configure the API base URL and the number
/// of results (k) to fetch per query.  Values are persisted in memory only
/// for the lifetime of the app.
class SettingsDialog extends StatefulWidget {
  final String currentBaseUrl;
  final int currentK;
  final void Function(String baseUrl, int k) onSave;

  const SettingsDialog({
    super.key,
    required this.currentBaseUrl,
    required this.currentK,
    required this.onSave,
  });

  @override
  State<SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<SettingsDialog> {
  late final TextEditingController _urlCtrl;
  late final TextEditingController _kCtrl;

  @override
  void initState() {
    super.initState();
    _urlCtrl = TextEditingController(text: widget.currentBaseUrl);
    _kCtrl = TextEditingController(text: widget.currentK.toString());
  }

  @override
  void dispose() {
    _urlCtrl.dispose();
    _kCtrl.dispose();
    super.dispose();
  }

  void _save() {
    final url = _urlCtrl.text.trim();
    final k = int.tryParse(_kCtrl.text.trim()) ?? 5;
    widget.onSave(url, k.clamp(1, 50));
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Settings'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _urlCtrl,
            decoration: const InputDecoration(
              labelText: 'API Base URL',
              hintText: 'http://10.0.2.2:8001',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.link),
            ),
            keyboardType: TextInputType.url,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _kCtrl,
            decoration: const InputDecoration(
              labelText: 'Results (k)',
              hintText: '5',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.format_list_numbered),
            ),
            keyboardType: TextInputType.number,
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _save,
          child: const Text('Save'),
        ),
      ],
    );
  }
}
