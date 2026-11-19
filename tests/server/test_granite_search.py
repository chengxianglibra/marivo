"""Granite document encoding stays independent of scheduling batches."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from marivo.server.granite_search import GraniteEncoder


@dataclass(frozen=True)
class _Tokens:
    ids: list[int]
    attention_mask: list[int]


class _Tokenizer:
    def encode_batch(self, inputs: list[str]) -> list[_Tokens]:
        identities = {"first document": 2, "second document": 5, "third document": 11}
        return [_Tokens([identities[text]], [1]) for text in inputs]


class _PeerDependentSession:
    def run(self, outputs: None, inputs: dict[str, npt.NDArray[np.int64]]) -> list[object]:
        ids = inputs["input_ids"]
        hidden = np.zeros((*ids.shape, 384), dtype=np.float32)
        # Share a data-dependent value across the whole feed as INT8 kernels do.
        hidden[:, 0, 0] = ids[:, 0]
        hidden[:, 0, 1] = np.max(ids, axis=None)
        hidden[:, 0, 2] = 1.0
        return [hidden]


def test_granite_document_vectors_do_not_depend_on_scheduled_peers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokenizer, session = _Tokenizer(), _PeerDependentSession()
    monkeypatch.setattr(GraniteEncoder, "_load_engine", lambda self: (tokenizer, session))
    encoder = GraniteEncoder(Path("unused.onnx"))
    texts = ["first document", "second document", "third document"]

    isolated = np.concatenate([encoder.encode([text]) for text in texts], axis=0)
    grouped = encoder.encode(texts)
    reordered = encoder.encode([texts[2], texts[0], texts[1]])

    assert isolated[0].tobytes() != isolated[1].tobytes()
    assert grouped.tobytes() == isolated.tobytes()
    assert reordered.tobytes() == isolated[[2, 0, 1]].tobytes()
