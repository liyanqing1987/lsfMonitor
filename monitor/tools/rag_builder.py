# -*- coding: utf-8 -*-
################################
# File Name   : rag_builder.py
# Author      : liyanqing.1987
# Created On  : 2026-05-01 00:00:00
# Description : Build/update RAG vector database (rag_chunks.json + rag_faiss.index)
#               from PDF, text, markdown, and reStructuredText files.
################################
import os
import sys
import json
import time
import argparse

import numpy as np

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common

from common import common_config

config = common_config.load_config()

os.environ['PYTHONUNBUFFERED'] = '1'

SUPPORTED_EXTENSIONS = {'.pdf', '.txt', '.md', '.rst'}


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser(description='Build/update RAG vector database for AI helpdesk.', formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('-i', '--input_files',
                        nargs='+',
                        default=[],
                        help='Input files or directories (directories are scanned recursively for .pdf/.txt/.md/.rst).')
    parser.add_argument('-l', '--list',
                        action='store_true',
                        default=False,
                        help='List all documents indexed in the RAG database.')
    parser.add_argument('-d', '--delete',
                        nargs='+',
                        default=[],
                        help='Delete documents from the RAG database (match by filename substring).')
    parser.add_argument('--rebuild',
                        action='store_true',
                        default=False,
                        help='Discard existing data and rebuild from scratch (default: append mode).')
    parser.add_argument('--chunk_size',
                        type=int,
                        default=700,
                        help='Chunk size in characters (default: 700).')
    parser.add_argument('--chunk_overlap',
                        type=int,
                        default=100,
                        help='Chunk overlap in characters (default: 100).')
    parser.add_argument('-o', '--output_dir',
                        default='',
                        help='Output directory for RAG files (default: $LSFMONITOR_INSTALL_PATH/db/ai).')
    parser.add_argument('--prefix',
                        default='rag',
                        help='Filename prefix for output files (default: rag, producing rag_chunks.json etc.).')
    parser.add_argument('--compress',
                        choices=['flat', 'sq8', 'sq6', 'sq4', 'pq256', 'pq128', 'pq64'],
                        default='flat',
                        help='FAISS index type (default: flat).\n'
                        'Benchmark on 15K vectors:\n'
                        '         Recall@1  @5     @10    size):\n'
                        '  flat=  100%%      100%%   100%%   118MB\n'
                        '  sq8=   100%%      99.8%%  99.9%%  30MB\n'
                        '  sq6=   100%%      99.5%%  99.4%%  22MB\n'
                        '  sq4=   99%%       97.1%%  97.2%%  15MB\n'
                        '  pq256= 97%%       87.6%%  87.2%%  6MB\n'
                        '  pq128= 97%%       79.3%%  79%%    4MB\n'
                        '  pq64=  92.5%%     67.5%%  65.7%%  3MB')
    parser.add_argument('--batch_size',
                        type=int,
                        default=10,
                        help='Number of chunks per embedding API call (default: 10).')
    parser.add_argument('--workers',
                        type=int,
                        default=10,
                        help='Number of concurrent workers for embedding API calls (default: 10).')

    args = parser.parse_args()

    if not args.input_files and not args.list and not args.delete:
        parser.error('one of -i/--input_files, -l/--list, or -d/--delete is required.')

    return args


class RagBuilder():
    def __init__(self, args):
        self.input_files = args.input_files
        self.rebuild = args.rebuild
        self.chunk_size = args.chunk_size
        self.chunk_overlap = args.chunk_overlap
        self.batch_size = args.batch_size
        self.workers = args.workers
        self.compress = args.compress

        self.output_dir = args.output_dir if args.output_dir else os.path.join(os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'db', 'ai')
        prefix = args.prefix
        self.chunks_file = os.path.join(self.output_dir, f'{prefix}_chunks.json')
        self.faiss_file = os.path.join(self.output_dir, f'{prefix}_faiss.index')
        self.metadata_file = os.path.join(self.output_dir, f'{prefix}_metadata.json')
        self.embeddings_file = os.path.join(self.output_dir, f'{prefix}_embeddings.npy')

        # Resolve embedding API config (fall back to main AI config if embedding-specific is empty).
        self.api_base_url = getattr(config, 'ai_embedding_api_base_url', '') or getattr(config, 'ai_api_base_url', '')
        self.api_key = getattr(config, 'ai_embedding_api_key', '') or getattr(config, 'ai_api_key', '')
        self.embedding_model = getattr(config, 'ai_embedding_model_name', '')

    def collect_files(self):
        """Recursively collect files with supported extensions from input paths."""
        collected = []

        for path in self.input_files:
            path = os.path.abspath(path)

            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()

                if ext in SUPPORTED_EXTENSIONS:
                    collected.append(path)
                else:
                    common.bprint(f'Skipping unsupported file: {path}', level='Warning')
            elif os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for fname in sorted(files):
                        ext = os.path.splitext(fname)[1].lower()

                        if ext in SUPPORTED_EXTENSIONS:
                            collected.append(os.path.join(root, fname))
            else:
                common.bprint(f'Path not found: {path}', level='Warning')

        # Deduplicate while preserving order.
        seen = set()
        unique = []

        for f in collected:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique

    def load_existing_data(self):
        """Load existing RAG data for append mode. Returns (chunks, metadata, embeddings)."""
        chunks = []
        metadata = []
        embeddings = None

        if self.rebuild:
            return chunks, metadata, embeddings

        if os.path.exists(self.chunks_file):
            try:
                with open(self.chunks_file, 'r', errors='replace') as f:
                    chunks = json.load(f)
            except Exception as error:
                common.bprint(f'Failed to load {self.chunks_file}: {error}', level='Warning')
                chunks = []

        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r', errors='replace') as f:
                    metadata = json.load(f)
            except Exception as error:
                common.bprint(f'Failed to load {self.metadata_file}: {error}', level='Warning')
                metadata = []

        if os.path.exists(self.embeddings_file):
            try:
                embeddings = np.load(self.embeddings_file)
            except Exception as error:
                common.bprint(f'Failed to load {self.embeddings_file}: {error}', level='Warning')
                embeddings = None

        # Validate consistency.
        if chunks and embeddings is not None:
            if len(chunks) != embeddings.shape[0]:
                common.bprint('Mismatch between chunks and embeddings count, will rebuild embeddings.', level='Warning')
                embeddings = None
                metadata = []

        if chunks and metadata and len(chunks) != len(metadata):
            common.bprint('Mismatch between chunks and metadata count, clearing metadata.', level='Warning')
            metadata = []

        # If old data exists without embeddings cache, user must --rebuild.
        if chunks and embeddings is None:
            common.bprint(f'Existing {os.path.basename(self.chunks_file)} found but no {os.path.basename(self.embeddings_file)} (first migration from old data).', level='Error')
            common.bprint('Please use --rebuild and provide all source files to regenerate.', level='Error')
            sys.exit(1)

        return chunks, metadata, embeddings

    def get_existing_sources(self, metadata):
        """Get set of source file paths already indexed."""
        sources = set()

        for entry in metadata:
            if isinstance(entry, dict) and 'source' in entry:
                sources.add(entry['source'])

        return sources

    def extract_text(self, file_path):
        """Extract text from a file. Returns list of (text, page_number) tuples."""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.pdf':
            return self._extract_pdf(file_path)
        else:
            return self._extract_text_file(file_path)

    def _extract_pdf(self, file_path):
        """Extract text from PDF using pypdf."""
        try:
            from pypdf import PdfReader
        except ImportError:
            common.bprint('pypdf is required for PDF support. Install with: pip install pypdf', level='Error')
            return []

        results = []

        try:
            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            common.bprint(f'  {os.path.basename(file_path)}: {total_pages} pages', date_format='%Y-%m-%d %H:%M:%S')

            for i, page in enumerate(reader.pages):
                text = page.extract_text()

                if text and text.strip():
                    results.append((text, i + 1))

                if (i + 1) % 100 == 0 or (i + 1) == total_pages:
                    common.bprint(f'    Extracted {i + 1}/{total_pages} pages ...', date_format='%Y-%m-%d %H:%M:%S')
        except Exception as error:
            common.bprint(f'Failed to read PDF {file_path}: {error}', level='Warning')

        return results

    def _extract_text_file(self, file_path):
        """Extract text from plain text/markdown/rst file."""
        results = []

        try:
            with open(file_path, 'r', errors='replace') as f:
                text = f.read()

            if text.strip():
                results.append((text, None))
        except Exception as error:
            common.bprint(f'Failed to read {file_path}: {error}', level='Warning')

        return results

    def chunk_text(self, text):
        """Split text into chunks at paragraph/sentence boundaries."""
        chunks = []

        # Split into paragraphs first.
        paragraphs = text.split('\n\n')
        current_chunk = ''

        for para in paragraphs:
            para = para.strip()

            if not para:
                continue

            if len(current_chunk) + len(para) + 2 <= self.chunk_size:
                if current_chunk:
                    current_chunk += '\n\n' + para
                else:
                    current_chunk = para
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                    # Keep overlap from end of previous chunk.
                    if self.chunk_overlap > 0 and len(current_chunk) > self.chunk_overlap:
                        overlap_text = current_chunk[-self.chunk_overlap:]
                        current_chunk = overlap_text + '\n\n' + para
                    else:
                        current_chunk = para
                else:
                    # Single paragraph exceeds chunk_size, split by sentences.
                    sub_chunks = self._split_long_text(para)
                    chunks.extend(sub_chunks[:-1])
                    current_chunk = sub_chunks[-1] if sub_chunks else ''

        if current_chunk.strip():
            chunks.append(current_chunk)

        return chunks

    def _split_long_text(self, text):
        """Split a long text block into chunks, trying sentence boundaries."""
        chunks = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size

            if end >= len(text):
                chunks.append(text[start:])
                break

            # Try to find a sentence boundary.
            boundary = -1

            for sep in ['. ', '。', '! ', '? ', '\n']:
                pos = text.rfind(sep, start, end)

                if pos > start and pos > boundary:
                    boundary = pos + len(sep)

            if boundary > start:
                chunks.append(text[start:boundary])
                new_start = boundary - self.chunk_overlap if self.chunk_overlap > 0 else boundary
                # Ensure forward progress: overlap must not push start backwards.
                start = new_start if new_start > start else boundary
            else:
                # No boundary found, split at space or hard cut.
                space_pos = text.rfind(' ', start, end)

                if space_pos > start:
                    chunks.append(text[start:space_pos])
                    new_start = space_pos + 1 - self.chunk_overlap if self.chunk_overlap > 0 else space_pos + 1
                    start = new_start if new_start > start else space_pos + 1
                else:
                    chunks.append(text[start:end])
                    new_start = end - self.chunk_overlap if self.chunk_overlap > 0 else end
                    start = new_start if new_start > start else end

        return chunks

    def _get_embeddings_batch(self, batch_texts, url, headers, session):
        """Try to get embeddings for multiple texts in a single API call.
        Returns list of embeddings on success, None if batch mode is not supported.
        """
        payload = {
            'model': self.embedding_model,
            'input': [{'type': 'text', 'text': t} for t in batch_texts]
        }

        for attempt in range(3):
            try:
                resp = session.post(url, headers=headers, json=payload, timeout=60)

                if resp.status_code == 200:
                    data = resp.json().get('data', None)

                    if data is None:
                        return None

                    # Response format: {"data": [{"embedding": [...]}, ...]}
                    if isinstance(data, list) and len(data) == len(batch_texts):
                        return [item['embedding'] for item in data]

                    # Response format: {"data": {"embedding": [...]}} — single only, batch not supported.
                    if isinstance(data, dict):
                        return None

                    return None
                else:
                    common.bprint(f'    Batch API returned status {resp.status_code}, attempt {attempt + 1}/3', level='Warning')
            except Exception as error:
                common.bprint(f'    Batch request failed: {error}, attempt {attempt + 1}/3', level='Warning')

            if attempt < 2:
                time.sleep(2 ** attempt)

        return None

    def _get_embedding_single(self, text, url, headers, session):
        """Get embedding for a single text. Returns embedding list or exits on failure."""
        payload = {
            'model': self.embedding_model,
            'input': [{'type': 'text', 'text': text}]
        }

        for attempt in range(3):
            try:
                resp = session.post(url, headers=headers, json=payload, timeout=30)

                if resp.status_code == 200:
                    data = resp.json().get('data', {})

                    # Handle both {"data": {"embedding": [...]}} and {"data": [{"embedding": [...]}]}
                    if isinstance(data, dict):
                        return data['embedding']
                    elif isinstance(data, list) and len(data) > 0:
                        return data[0]['embedding']
                else:
                    common.bprint(f'    API returned status {resp.status_code}, attempt {attempt + 1}/3', level='Warning')
            except Exception as error:
                common.bprint(f'    Request failed: {error}, attempt {attempt + 1}/3', level='Warning')

            if attempt < 2:
                time.sleep(2 ** attempt)

        common.bprint('Failed to get embedding after 3 attempts, aborting.', level='Error')
        sys.exit(1)

    def get_embeddings(self, texts):
        """Get embeddings for a list of texts via the embedding API. Returns numpy array.
        Automatically tries batch mode first; falls back to concurrent single-text mode if unsupported.
        """
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not self.api_base_url or not self.api_key or not self.embedding_model:
            common.bprint('Embedding API not configured. Set ai_embedding_* or ai_api_* in config.', level='Error')
            sys.exit(1)

        base_url = self.api_base_url.rstrip('/')
        url = base_url + '/embeddings/multimodal'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        session = requests.Session()

        total = len(texts)
        all_embeddings = [None] * total  # Pre-allocate to preserve order.
        done_count = 0
        total_batches = (total + self.batch_size - 1) // self.batch_size
        use_batch = None  # None = not yet probed, True/False = detected.

        common.bprint(f'  Total: {total} chunks, batch_size={self.batch_size}, workers={self.workers}', date_format='%Y-%m-%d %H:%M:%S')

        # Probe batch mode on the first batch.
        first_batch = texts[:min(self.batch_size, total)]

        if len(first_batch) > 1:
            result = self._get_embeddings_batch(first_batch, url, headers, session)

            if result is not None:
                use_batch = True
                common.bprint('  Batch embedding mode detected, using batch API.', date_format='%Y-%m-%d %H:%M:%S')
            else:
                use_batch = False
                common.bprint(f'  Batch embedding not supported, using concurrent single-text mode (workers={self.workers}).', date_format='%Y-%m-%d %H:%M:%S')

        if use_batch:
            # Batch mode: send batch_size texts per API call, parallelize across batches.
            batch_ranges = []

            for batch_idx in range(total_batches):
                s = batch_idx * self.batch_size
                e = min(s + self.batch_size, total)
                batch_ranges.append((batch_idx, s, e))

            # First batch already done during probe.
            first_s, first_e = batch_ranges[0][1], batch_ranges[0][2]

            for i, emb in enumerate(result):
                all_embeddings[first_s + i] = emb

            done_count = first_e - first_s
            common.bprint(f'  Progress: {done_count}/{total} ...', date_format='%Y-%m-%d %H:%M:%S')
            remaining_ranges = batch_ranges[1:]

            def _batch_worker(batch_info):
                _, s, e = batch_info
                return (s, self._get_embeddings_batch(texts[s:e], url, headers, session))

            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {executor.submit(_batch_worker, br): br for br in remaining_ranges}

                for future in as_completed(futures):
                    s, batch_result = future.result()
                    br = futures[future]
                    _, _, e = br

                    if batch_result is not None:
                        for i, emb in enumerate(batch_result):
                            all_embeddings[s + i] = emb
                    else:
                        # Fallback: sequential single-text for this batch.
                        common.bprint(f'  Batch call failed at offset {s}, falling back to single-text.', level='Warning')

                        for i, text in enumerate(texts[s:e]):
                            emb = self._get_embedding_single(text, url, headers, session)
                            all_embeddings[s + i] = emb

                    done_count += (e - s)

                    if done_count % (self.batch_size * 5) < self.batch_size or done_count == total:
                        common.bprint(f'  Progress: {done_count}/{total} ...', date_format='%Y-%m-%d %H:%M:%S')
        else:
            # Concurrent single-text mode.
            def _single_worker(idx_text):
                idx, text = idx_text
                return (idx, self._get_embedding_single(text, url, headers, session))

            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {executor.submit(_single_worker, (i, t)): i for i, t in enumerate(texts)}

                for future in as_completed(futures):
                    idx, emb = future.result()
                    all_embeddings[idx] = emb
                    done_count += 1

                    if done_count % 100 == 0 or done_count == total:
                        common.bprint(f'  Progress: {done_count}/{total} ...', date_format='%Y-%m-%d %H:%M:%S')

        return np.array(all_embeddings, dtype=np.float32)

    def build_faiss_index(self, embeddings):
        """Build a FAISS index from L2-normalized embeddings."""
        import faiss

        # L2-normalize so inner product == cosine similarity.
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = embeddings / norms

        dim = normalized.shape[1]
        sq_types = {
            'sq8': faiss.ScalarQuantizer.QT_8bit,
            'sq6': faiss.ScalarQuantizer.QT_6bit,
            'sq4': faiss.ScalarQuantizer.QT_4bit,
        }

        pq_types = {'pq64': 64, 'pq128': 128, 'pq256': 256}

        if self.compress in sq_types:
            index = faiss.IndexScalarQuantizer(dim, sq_types[self.compress], faiss.METRIC_INNER_PRODUCT)
            index.train(normalized)
            index.add(normalized)
        elif self.compress in pq_types:
            min_train = 256  # PQ needs at least 256 training points per sub-quantizer.

            if normalized.shape[0] < min_train:
                common.bprint(f'PQ requires at least {min_train} vectors but only {normalized.shape[0]} available, falling back to flat.', level='Warning')
                index = faiss.IndexFlatIP(dim)
                index.add(normalized)
            else:
                index = faiss.IndexPQ(dim, pq_types[self.compress], 8, faiss.METRIC_INNER_PRODUCT)
                index.train(normalized)
                index.add(normalized)
        else:
            index = faiss.IndexFlatIP(dim)
            index.add(normalized)

        return index

    def save(self, chunks, metadata, embeddings, faiss_index):
        """Save all output files to the output directory."""
        import faiss

        os.makedirs(self.output_dir, exist_ok=True)

        with open(self.chunks_file, 'w') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

        common.bprint(f'  Saved {self.chunks_file} ({len(chunks)} chunks)')

        faiss.write_index(faiss_index, self.faiss_file)
        common.bprint(f'  Saved {self.faiss_file}')

        with open(self.metadata_file, 'w') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        common.bprint(f'  Saved {self.metadata_file}')

        np.save(self.embeddings_file, embeddings)
        common.bprint(f'  Saved {self.embeddings_file}')

    def list_sources(self):
        """List all documents indexed in the RAG database."""
        if not os.path.exists(self.metadata_file):
            common.bprint(f'No metadata file found: {self.metadata_file}', level='Warning')
            common.bprint('RAG database may not exist yet, or was built without metadata.')
            return

        try:
            with open(self.metadata_file, 'r', errors='replace') as f:
                metadata = json.load(f)
        except Exception as error:
            common.bprint(f'Failed to load {self.metadata_file}: {error}', level='Error')
            return

        # Aggregate by source.
        source_stats = {}

        for entry in metadata:
            if not isinstance(entry, dict) or 'source' not in entry:
                continue

            source = entry['source']

            if source not in source_stats:
                source_stats[source] = {'chunks': 0, 'pages': set()}

            source_stats[source]['chunks'] += 1

            if 'page' in entry:
                source_stats[source]['pages'].add(entry['page'])

        if not source_stats:
            common.bprint('No indexed documents found in metadata.')
            return

        common.bprint(f'=== Indexed Documents ({len(source_stats)} files, {len(metadata)} chunks) ===')

        for idx, (source, stats) in enumerate(sorted(source_stats.items()), 1):
            exists = 'exists' if os.path.exists(source) else 'MISSING'
            pages_info = f', {len(stats["pages"])} pages' if stats['pages'] else ''
            common.bprint(f'  [{idx}] {source}')
            common.bprint(f'      {stats["chunks"]} chunks{pages_info}, file {exists}')

    def delete_sources(self, patterns):
        """Delete documents matching patterns from the RAG database."""
        # Load existing data.
        if not os.path.exists(self.metadata_file) or not os.path.exists(self.chunks_file):
            common.bprint('RAG database not found or incomplete.', level='Error')
            return

        try:
            with open(self.chunks_file, 'r', errors='replace') as f:
                chunks = json.load(f)

            with open(self.metadata_file, 'r', errors='replace') as f:
                metadata = json.load(f)

            embeddings = None

            if os.path.exists(self.embeddings_file):
                embeddings = np.load(self.embeddings_file)
        except Exception as error:
            common.bprint(f'Failed to load RAG data: {error}', level='Error')
            return

        if len(chunks) != len(metadata):
            common.bprint('Chunks and metadata count mismatch, cannot proceed.', level='Error')
            return

        if embeddings is not None and embeddings.shape[0] != len(chunks):
            common.bprint('Embeddings and chunks count mismatch, cannot proceed.', level='Error')
            return

        # Find chunks to delete (match source path by substring).
        delete_indices = set()
        matched_sources = set()

        for i, entry in enumerate(metadata):
            if not isinstance(entry, dict) or 'source' not in entry:
                continue

            source = entry['source']

            for pattern in patterns:
                if pattern in source:
                    delete_indices.add(i)
                    matched_sources.add(source)
                    break

        if not delete_indices:
            common.bprint(f'No matching documents found for: {patterns}')
            common.bprint('Use -l/--list to see indexed documents.')
            return

        common.bprint(f'Deleting {len(matched_sources)} document(s), {len(delete_indices)} chunks:')

        for source in sorted(matched_sources):
            common.bprint(f'  - {source}')

        # Filter out deleted indices.
        keep_indices = [i for i in range(len(chunks)) if i not in delete_indices]
        new_chunks = [chunks[i] for i in keep_indices]
        new_metadata = [metadata[i] for i in keep_indices]
        new_embeddings = embeddings[keep_indices] if embeddings is not None else None

        if not new_chunks:
            # All chunks deleted, remove all files.
            for f in [self.chunks_file, self.faiss_file, self.metadata_file, self.embeddings_file]:
                if os.path.exists(f):
                    os.remove(f)
                    common.bprint(f'  Removed {f}')

            common.bprint('All documents deleted, RAG database removed.')
            return

        # Rebuild FAISS index.
        common.bprint('Rebuilding FAISS index...')
        faiss_index = self.build_faiss_index(new_embeddings)

        # Save.
        common.bprint('Saving...')
        self.save(new_chunks, new_metadata, new_embeddings, faiss_index)
        common.bprint(f'=== Done. Remaining: {len(new_chunks)} chunks ===')

    def run(self):
        """Main execution flow."""
        common.bprint('=== RAG Builder ===')

        # 1. Collect input files.
        common.bprint('Collecting input files...')
        file_list = self.collect_files()

        if not file_list:
            common.bprint('No supported files found in input paths.', level='Error')
            sys.exit(1)

        common.bprint(f'  Found {len(file_list)} file(s)')

        # 2. Load existing data (append mode).
        existing_chunks, existing_metadata, existing_embeddings = self.load_existing_data()

        if existing_chunks:
            common.bprint(f'  Loaded {len(existing_chunks)} existing chunks')

        # 3. Determine which files to process (skip already-indexed in append mode).
        existing_sources = self.get_existing_sources(existing_metadata)
        new_files = [f for f in file_list if f not in existing_sources]

        if not new_files:
            common.bprint('All input files are already indexed. Nothing to do.')
            common.bprint('  Use --rebuild to force regeneration.')
            return

        if len(new_files) < len(file_list):
            common.bprint(f'  Skipping {len(file_list) - len(new_files)} already-indexed file(s)')

        common.bprint(f'  Processing {len(new_files)} new file(s)')

        # 4. Extract text and chunk.
        common.bprint('Extracting text and chunking...')
        new_chunks = []
        new_metadata = []

        for file_path in new_files:
            text_pages = self.extract_text(file_path)

            if not text_pages:
                common.bprint(f'  No text extracted from: {file_path}', level='Warning')
                continue

            file_chunks_count = 0
            total_text_pages = len(text_pages)

            for tp_idx, (text, page_num) in enumerate(text_pages):
                chunks = self.chunk_text(text)

                for chunk in chunks:
                    new_chunks.append(chunk)
                    meta = {'source': file_path}

                    if page_num is not None:
                        meta['page'] = page_num

                    new_metadata.append(meta)
                    file_chunks_count += 1

                if (tp_idx + 1) % 100 == 0 or (tp_idx + 1) == total_text_pages:
                    common.bprint(f'    Chunked {tp_idx + 1}/{total_text_pages} pages ({file_chunks_count} chunks) ...', date_format='%Y-%m-%d %H:%M:%S')

            common.bprint(f'  {os.path.basename(file_path)}: {file_chunks_count} chunks')

        if not new_chunks:
            common.bprint('No text could be extracted from input files.', level='Error')
            sys.exit(1)

        common.bprint(f'  Total new chunks: {len(new_chunks)}', date_format='%Y-%m-%d %H:%M:%S')

        # 5. Generate embeddings.
        common.bprint('Generating embeddings...', date_format='%Y-%m-%d %H:%M:%S')
        new_embeddings = self.get_embeddings(new_chunks)
        common.bprint(f'  Generated {new_embeddings.shape[0]} embeddings (dim={new_embeddings.shape[1]})', date_format='%Y-%m-%d %H:%M:%S')

        # 6. Merge with existing data.
        all_chunks = existing_chunks + new_chunks
        all_metadata = existing_metadata + new_metadata

        if existing_embeddings is not None and existing_embeddings.shape[0] > 0:
            all_embeddings = np.vstack([existing_embeddings, new_embeddings])
        else:
            all_embeddings = new_embeddings

        # 7. Build FAISS index.
        common.bprint('Building FAISS index...', date_format='%Y-%m-%d %H:%M:%S')
        faiss_index = self.build_faiss_index(all_embeddings)
        common.bprint(f'  Index contains {faiss_index.ntotal} vectors', date_format='%Y-%m-%d %H:%M:%S')

        # 8. Save.
        common.bprint('Saving output files...', date_format='%Y-%m-%d %H:%M:%S')
        self.save(all_chunks, all_metadata, all_embeddings, faiss_index)

        common.bprint(f'=== Done. Total chunks: {len(all_chunks)} ===', date_format='%Y-%m-%d %H:%M:%S')


################
# Main Process #
################
def main():
    args = read_args()
    rag_builder = RagBuilder(args)

    if args.list:
        rag_builder.list_sources()
    elif args.delete:
        rag_builder.delete_sources(args.delete)
    else:
        rag_builder.run()


if __name__ == '__main__':
    main()
