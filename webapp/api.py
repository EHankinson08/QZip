#!/usr/bin/env python3
"""QZip local API server (FastAPI, 127.0.0.1:8000), used by the web UI. Every compress goes through the verified path
(src/safe_compress.py). The 2025 engines (optional `pip install -e .[legacy]`) are loaded only for the legacy modes
and for decoding old 2025 .qc files."""

import os
os.environ.setdefault("QZIP_NO_RAY", "1")      # no local Ray cluster needed
os.environ.setdefault("QZIP_LOG_FILE", "")     # no engine log file in CWD
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base64
import logging
from datetime import datetime
from typing import Dict, Any, List, Union, Optional

from qzip.core import _LazyEngine
from src.safe_compress import safe_compress, safe_decompress, MODES, DEFAULT_MODE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="QZip local API", description="Verified lossless compression", version="1.2.0",
              docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

quantum_enterprise = _LazyEngine()   # legacy 2025 engine, built on first use only

# Pydantic models
class CompressionRequest(BaseModel):
    data: str  # Base64 encoded data
    method: str = "hybrid_quantum"   # legacy UI value; mapped to a QZip mode below
    mode: Optional[str] = None       # fast (default)|max|ultra|auto|legacy|hybrid|text

class DecompressionRequest(BaseModel):
    compressed_data: str  # Base64 encoded compressed data
    compression_method: str = "hybrid_quantum"
    reconstruction_metadata: Optional[str] = None
    quantum_parameters: Optional[Dict[str, Any]] = None

class BatchCompressionRequest(BaseModel):
    items: List[Dict[str, Any]]

class CompressionResponse(BaseModel):
    compressed_data: str
    compression_method: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    reconstruction_metadata: Optional[str] = None
    quantum_parameters: Dict[str, Any]
    achieves_target: bool
    timestamp: str
    enterprise_features: Dict[str, Any]

class DecompressionResponse(BaseModel):
    decompressed_data: str  # Base64 encoded
    decompression_method: str
    original_size: int
    decompressed_size: int
    timestamp: str

# API Endpoints
@app.get("/")
async def root():
    return {"message": "QZip local API", "version": "1.2.0", "status": "operational",
            "default_mode": DEFAULT_MODE, "modes": sorted(MODES)}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/methods")
async def get_compression_methods():
    return {"modes": sorted(MODES), "default": DEFAULT_MODE}

@app.post("/compress", response_model=CompressionResponse)
def compress_data(request: CompressionRequest):
    """Compress with the verified QZip path (src/safe_compress.py): every candidate is decoded and
    sha256-checked before it can be returned; stored_xz is the guaranteed-lossless fallback."""
    try:
        data_bytes = base64.b64decode(request.data)
        mode = request.mode or (request.method if request.method in MODES else DEFAULT_MODE)
        logger.info(f"Compressing {len(data_bytes):,} bytes (mode={mode})")
        container, info = safe_compress(quantum_enterprise, data_bytes, mode=mode)
        qp = dict(container["quantum_parameters"]); qp["qzip"] = dict(qp["qzip"], tried=info["tried"], mode=mode)
        payload = max(1, info["payload"])
        logger.info(f"Compression complete: {info['chosen']} {len(data_bytes)/payload:.2f}:1 (verified byte-exact)")
        return CompressionResponse(
            compressed_data=container["compressed_data"],
            compression_method=container["method"],
            original_size=len(data_bytes),
            compressed_size=payload,
            compression_ratio=len(data_bytes) / payload,
            reconstruction_metadata=container["reconstruction_metadata"],
            quantum_parameters=qp,
            achieves_target=False,
            timestamp=datetime.now().isoformat(),
            enterprise_features={"verified_roundtrip": True}
        )
    except Exception as e:
        logger.error(f"Compression error: {e}")
        raise HTTPException(status_code=500, detail=f"Compression failed: {str(e)}")

@app.post("/decompress", response_model=DecompressionResponse)
def decompress_data(request: DecompressionRequest):
    """Decode a QZip container (new verified containers and older web-app .qc files)."""
    try:
        container = {
            "compressed_data": request.compressed_data,
            "method": request.compression_method,
            "reconstruction_metadata": request.reconstruction_metadata,
            "quantum_parameters": request.quantum_parameters or {},
        }
        out = safe_decompress(quantum_enterprise, container)   # raises if sha256 recorded and mismatched
        logger.info(f"Decompression complete: {len(out):,} bytes")
        return DecompressionResponse(
            decompressed_data=base64.b64encode(out).decode("utf-8"),
            decompression_method=request.compression_method,
            original_size=len(base64.b64decode(request.compressed_data)),
            decompressed_size=len(out),
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        logger.error(f"Decompression error: {e}")
        raise HTTPException(status_code=500, detail=f"Decompression failed: {str(e)}")

@app.post("/compress/batch")
async def batch_compress(request: BatchCompressionRequest):
    """Batch compress multiple data items"""
    try:
        logger.info(f"🔬 Batch compressing {len(request.items)} items")
        
        results = []
        total_original_size = 0
        total_compressed_size = 0
        
        for item in request.items:
            try:
                # Decode base64 data
                data_bytes = base64.b64decode(item['data'])
                method = item.get('method', 'hybrid_quantum')
                
                # Compress (verified QZip path)
                container, info = safe_compress(quantum_enterprise, data_bytes,
                                                mode=item.get('mode') or (method if method in MODES else DEFAULT_MODE))
                result = {'original_size': len(data_bytes), 'compressed_size': max(1, info['payload'])}
                results.append({
                    'id': item['id'],
                    'status': 'success',
                    'compressed_data': container['compressed_data'],
                    'reconstruction_metadata': container['reconstruction_metadata'],
                    'compression_method': container['method'],
                    'quantum_parameters': container['quantum_parameters'],
                    'original_size': len(data_bytes),
                    'compressed_size': result['compressed_size'],
                    'compression_ratio': len(data_bytes) / result['compressed_size'],
                    'verified_roundtrip': True
                })
                
                total_original_size += result['original_size']
                total_compressed_size += result['compressed_size']
                
            except Exception as e:
                results.append({
                    'id': item['id'],
                    'status': 'error',
                    'error': str(e)
                })
        
        overall_ratio = total_original_size / total_compressed_size if total_compressed_size > 0 else 1.0
        
        logger.info(f"✅ Batch compression complete: {len(request.items)} items, {overall_ratio:.1f}:1 overall ratio")
        
        return {
            'results': results,
            'total_original_size': total_original_size,
            'total_compressed_size': total_compressed_size,
            'overall_ratio': overall_ratio,
            'timestamp': datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Batch compression error: {e}")
        raise HTTPException(status_code=500, detail=f"Batch compression failed: {str(e)}")

if __name__ == "__main__":
    host = os.environ.get("QZIP_API_HOST", "127.0.0.1")   # local-only by default
    port = int(os.environ.get("QZIP_API_PORT", "8000"))
    logger.info(f"QZip API server on http://{host}:{port} (docs at /docs)")
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_keep_alive=300)
