import { useRef, useState } from "react";
import { X, Download, Upload, AlertTriangle, CheckCircle, Info } from "lucide-react";
import { exportConfig, importConfig, ImportResult } from "../api/client";

interface Props {
  onClose: () => void;
  onImported: () => void;
}

export function ConfigTransferModal({ onClose, onImported }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [exporting, setExporting]         = useState(false);
  const [importing, setImporting]         = useState(false);
  const [importResult, setImportResult]   = useState<ImportResult | null>(null);
  const [error, setError]                 = useState<string | null>(null);

  // -- Export ------------------------------------------------------------------

  const handleExport = async () => {
    setExporting(true);
    setError(null);
    try {
      const blob = await exportConfig();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = "cloudshell-config.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(String(err));
    } finally {
      setExporting(false);
    }
  };

  // -- Import ------------------------------------------------------------------

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setImporting(true);
    setImportResult(null);
    setError(null);

    try {
      const result = await importConfig(file);
      setImportResult(result);
      if (result.imported > 0) onImported();
    } catch (err) {
      setError(String(err));
    } finally {
      setImporting(false);
      // Reset the input so the same file can be re-selected after fixing issues
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60">
      <div className="bg-slate-900 border border-slate-700 rounded-lg shadow-xl w-full max-w-lg flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h2 className="font-semibold text-white text-sm">Configuration Export / Import</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 transition-colors"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 flex flex-col gap-5 overflow-y-auto">

          {/* Security warning */}
          <div className="flex gap-3 items-start bg-yellow-900/30 border border-yellow-700/50 rounded-md p-3 text-xs text-yellow-300">
            <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
            <span>
              The export file contains <strong>plaintext credentials</strong>.
              Store it securely and delete it after use.
            </span>
          </div>

          {/* Export section */}
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">Export</h3>
            <p className="text-xs text-slate-400">
              Download all device configurations (including decrypted credentials) as a JSON file.
              Use this file to migrate to a new CloudShell instance.
            </p>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="flex items-center justify-center gap-2 px-4 py-2 rounded-md text-xs
                         bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
                         text-white font-medium transition-colors self-start"
            >
              <Download size={13} />
              {exporting ? "Exporting..." : "Download config"}
            </button>
          </section>

          <hr className="border-slate-700" />

          {/* Import section */}
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">Import</h3>
            <p className="text-xs text-slate-400">
              Upload a config file exported from another CloudShell instance.
              Devices already present (matched by hostname, port, and username) will be skipped.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={handleFileChange}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={importing}
              className="flex items-center justify-center gap-2 px-4 py-2 rounded-md text-xs
                         bg-slate-700 hover:bg-slate-600 disabled:opacity-50 disabled:cursor-not-allowed
                         text-white font-medium transition-colors self-start"
            >
              <Upload size={13} />
              {importing ? "Importing..." : "Select config file"}
            </button>

            {/* Import result */}
            {importResult && (
              <div className="mt-1 flex flex-col gap-1.5 bg-slate-800 border border-slate-700 rounded-md p-3 text-xs">
                <div className="flex items-center gap-2 text-green-400 font-medium">
                  <CheckCircle size={13} />
                  Import complete
                </div>
                <div className="grid grid-cols-3 gap-2 text-slate-300 mt-1">
                  <span className="text-center">
                    <span className="block text-lg font-bold text-green-400">{importResult.imported}</span>
                    imported
                  </span>
                  <span className="text-center">
                    <span className="block text-lg font-bold text-yellow-400">{importResult.skipped}</span>
                    skipped
                  </span>
                  <span className="text-center">
                    <span className="block text-lg font-bold text-red-400">{importResult.errors}</span>
                    errors
                  </span>
                </div>
                {importResult.messages.length > 0 && (
                  <ul className="mt-1 flex flex-col gap-0.5 max-h-32 overflow-y-auto">
                    {importResult.messages.map((msg, i) => (
                      <li key={i} className="flex gap-1.5 items-start text-slate-400">
                        <Info size={11} className="mt-0.5 flex-shrink-0" />
                        {msg}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          {/* Global error */}
          {error && (
            <div className="flex gap-2 items-start bg-red-900/30 border border-red-700/50 rounded-md p-3 text-xs text-red-300">
              <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end px-5 py-3 border-t border-slate-700">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-md text-xs bg-slate-700 hover:bg-slate-600
                       text-slate-200 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
