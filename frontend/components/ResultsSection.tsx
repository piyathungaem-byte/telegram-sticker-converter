'use client';

import React from 'react';
import { Download, Sparkles, Smartphone, ChevronDown } from 'lucide-react';
import { PackFile } from '../lib/types';
import { api, resolveUrl } from '../lib/api';

interface ResultsSectionProps {
  packs: PackFile[];
  jobId: string;
}

export function ResultsSection({ packs, jobId }: ResultsSectionProps) {
  if (!packs || packs.length === 0) return null;

  return (
    <section className="p-6 rounded-2xl bg-panel border border-mint/30 shadow-2xl space-y-5 animate-in fade-in slide-in-from-bottom-4 duration-200">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-mint/20 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-mint" />
          </div>
          <div>
            <h3 className="font-extrabold text-base text-white">WhatsApp Sticker Packs Ready</h3>
            <p className="text-xs text-muted">
              Created {packs.length} WhatsApp-importable <code>.wastickers</code> bundle{packs.length === 1 ? '' : 's'}.
            </p>
          </div>
        </div>
      </div>

      {/* Packs Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {packs.map((pack, idx) => {
          const downloadUrl = api.getDownloadUrl(jobId, idx);
          const previews = (pack.previewUrls || []).slice(0, 6);
          return (
            <div
              key={pack.name || idx}
              className="p-4 rounded-xl bg-[#101618] border border-line flex flex-col justify-between gap-3.5"
            >
              <div>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <h4 className="font-bold text-sm text-white truncate">{pack.name}</h4>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-extrabold uppercase ${
                        pack.kind === 'Animated' ? 'bg-mint/20 text-mint' : 'bg-line text-muted'
                      }`}
                    >
                      {pack.kind}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-line text-[10px] font-bold text-muted">
                      {pack.count} stickers
                    </span>
                    <span className="px-2 py-0.5 rounded bg-line text-[10px] font-bold text-muted">
                      {pack.sizeLabel}
                    </span>
                  </div>
                </div>

                {/* Previews Strip */}
                <div className="flex items-center gap-1.5 overflow-x-auto py-1">
                  {previews.map((previewPath, pIdx) => (
                    <div
                      key={pIdx}
                      className="w-12 h-12 rounded-lg bg-[#0a0e10] border border-line flex items-center justify-center flex-shrink-0 p-1"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={resolveUrl(previewPath)}
                        alt="Converted"
                        className="max-w-full max-h-full object-contain"
                        loading="lazy"
                      />
                    </div>
                  ))}
                </div>
              </div>

              {/* Download CTA */}
              <a
                href={downloadUrl}
                download
                className="inline-flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-xl bg-mint hover:bg-mint-hover text-black font-extrabold text-xs shadow-md shadow-mint/10 transition"
              >
                <Download className="w-4 h-4" />
                <span>Download {pack.fileName}</span>
              </a>
            </div>
          );
        })}
      </div>

      {/* Accordion Import Guide */}
      <details className="p-4 rounded-xl bg-panel-elevated border border-line text-xs text-muted">
        <summary className="font-bold text-white cursor-pointer flex items-center gap-2 select-none">
          <Smartphone className="w-4 h-4 text-mint" />
          <span>How to import .wastickers into WhatsApp (Android & iOS)</span>
        </summary>
        <div className="pt-3 space-y-2 leading-relaxed text-[#c6d7d3]">
          <ol className="list-decimal list-inside space-y-1">
            <li>Download the <code>.wastickers</code> file above to your phone.</li>
            <li>
              On Android, open Google Play and install <b>Sticker Maker</b> (by Viko & Co) or any compatible sticker app.
            </li>
            <li>Tap the downloaded <code>.wastickers</code> file and choose to open with Sticker Maker.</li>
            <li>Tap <b>Add to WhatsApp</b>. Your stickers will appear in WhatsApp immediately!</li>
          </ol>
        </div>
      </details>
    </section>
  );
}
