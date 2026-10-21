"""A Mem0 vector store on AWS S3 Vectors.

Goes in: vectors, payloads and filters; comes out: stored records and nearest-neighbour results. It
talks to AWS and is not used by the DuckDB-only owner path.
"""

import json
import logging
from typing import Dict, List, Optional

from pydantic import BaseModel

from mem0.vector_stores.base import VectorStoreBase

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    raise ImportError(
        "The 'boto3' library is required. Please install it using 'pip install boto3'."
    )

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    """One search result: an id, a score and a payload, all optional.

    It carries data only and does nothing.
    """

    id: Optional[str]
    score: Optional[float]
    payload: Optional[Dict]


class S3Vectors(VectorStoreBase):
    """The S3 Vectors store: it creates the bucket index and inserts, searches, updates, deletes and lists vectors with metadata.

    It talks to AWS and never touches DuckDB.
    """

    def __init__(
        self,
        vector_bucket_name: str,
        collection_name: str,
        embedding_model_dims: int,
        distance_metric: str = "cosine",
        region_name: Optional[str] = None,
    ):
        """Create the AWS client and make sure the vector bucket and index exist.

        Goes in: bucket and collection names, vector width, distance metric and region; comes out: a
        ready store. It may create the bucket and index in AWS.
        """
        self.client = boto3.client("s3vectors", region_name=region_name)
        self.vector_bucket_name = vector_bucket_name
        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.distance_metric = distance_metric

        self._ensure_bucket_exists()
        self.create_col(self.collection_name, self.embedding_model_dims, self.distance_metric)

    def _ensure_bucket_exists(self):
        try:
            self.client.get_vector_bucket(vectorBucketName=self.vector_bucket_name)
            logger.info(f"Vector bucket '{self.vector_bucket_name}' already exists.")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NotFoundException":
                logger.info(f"Vector bucket '{self.vector_bucket_name}' not found. Creating it.")
                self.client.create_vector_bucket(vectorBucketName=self.vector_bucket_name)
                logger.info(f"Vector bucket '{self.vector_bucket_name}' created.")
            else:
                raise

    def create_col(self, name, vector_size, distance="cosine"):
        """Create the index with the given vector size and distance if it does not exist.

        Goes in: a name, a size and a distance; comes out: nothing. It does nothing when the index
        exists.
        """
        try:
            self.client.get_index(vectorBucketName=self.vector_bucket_name, indexName=name)
            logger.info(f"Index '{name}' already exists in bucket '{self.vector_bucket_name}'.")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NotFoundException":
                logger.info(
                    f"Index '{name}' not found in bucket '{self.vector_bucket_name}'. Creating it."
                )
                self.client.create_index(
                    vectorBucketName=self.vector_bucket_name,
                    indexName=name,
                    dataType="float32",
                    dimension=vector_size,
                    distanceMetric=distance,
                )
                logger.info(f"Index '{name}' created.")
            else:
                raise

    def _distance_to_score(self, raw_distance: Optional[float]) -> Optional[float]:
        if raw_distance is None:
            return None
        # Euclidean distance is unbounded, so 1 - distance would collapse most
        # scores to 0. Use a bounded monotonic map instead. Cosine distance is
        # in [0, 2], where 1 - distance is already a valid similarity.
        if self.distance_metric == "euclidean":
            return 1.0 / (1.0 + raw_distance)
        return max(0.0, 1.0 - raw_distance)

    def _parse_output(self, vectors: List[Dict]) -> List[OutputData]:
        results = []
        for v in vectors:
            payload = v.get("metadata", {})
            # Boto3 might return metadata as a JSON string
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse metadata for key {v.get('key')}")
                    payload = {}
            raw_distance = v.get("distance")
            score = self._distance_to_score(raw_distance)
            results.append(OutputData(id=v.get("key"), score=score, payload=payload))
        return results

    def insert(self, vectors, payloads=None, ids=None):
        """Put vectors with their metadata into the index under the given ids.

        Goes in: vectors, optional payloads and ids; comes out: nothing. Payloads default to empty.
        """
        vectors_to_put = []
        for i, vec in enumerate(vectors):
            vectors_to_put.append(
                {
                    "key": ids[i],
                    "data": {"float32": vec},
                    "metadata": payloads[i] if payloads else {},
                }
            )
        self.client.put_vectors(
            vectorBucketName=self.vector_bucket_name,
            indexName=self.collection_name,
            vectors=vectors_to_put,
        )

    def search(self, query, vectors, top_k=5, filters=None):
        """Find the nearest vectors to a query vector.

        Goes in: a query text (unused), the vector, a count and optional filters; comes out: a list
        of results with distances. It only reads.
        """
        params = {
            "vectorBucketName": self.vector_bucket_name,
            "indexName": self.collection_name,
            "queryVector": {"float32": vectors},
            "topK": top_k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if filters:
            params["filter"] = filters

        response = self.client.query_vectors(**params)
        return self._parse_output(response.get("vectors", []))

    def delete(self, vector_id):
        """Delete one vector by id.

        Goes in: the id; comes out: nothing. It removes only that key.
        """
        self.client.delete_vectors(
            vectorBucketName=self.vector_bucket_name,
            indexName=self.collection_name,
            keys=[vector_id],
        )

    def update(self, vector_id, vector=None, payload=None):
        # S3 Vectors uses put_vectors for updates (overwrite).
        # When vector=None (e.g. metadata-only update triggered by event=NONE),
        # fetch the existing vector data first to avoid passing None to boto3
        # which causes a parameter validation error:
        #   "Invalid type for parameter vectors[0].data.float32, value: None"
        """Overwrite one vector and its metadata.

        Goes in: the id, an optional new vector and an optional payload; comes out: nothing. When no
        vector is given it reads the stored one first so the write stays valid.
        """
        if vector is None:
            existing = self.get(vector_id)
            if existing is None:
                logger.warning(
                    f"update called with vector=None but {vector_id} not found; skipping"
                )
                return
            try:
                response = self.client.get_vectors(
                    vectorBucketName=self.vector_bucket_name,
                    indexName=self.collection_name,
                    keys=[vector_id],
                    returnData=True,
                    returnMetadata=True,
                )
                vectors = response.get("vectors", [])
                if not vectors:
                    logger.warning(f"update: no vector data found for {vector_id}; skipping")
                    return
                vector = vectors[0].get("data", {}).get("float32")
                if vector is None:
                    logger.warning(f"update: float32 data is None for {vector_id}; skipping")
                    return
                if payload is None:
                    payload = existing.payload
            except Exception as e:
                logger.error(
                    f"update: failed to fetch existing vector for {vector_id}: {e}", exc_info=True
                )
                return
        self.insert(vectors=[vector], payloads=[payload], ids=[vector_id])

    def get(self, vector_id) -> Optional[OutputData]:
        """Fetch one vector's metadata by id.

        Goes in: the id; comes out: a result, or None when the id is absent. It only reads.
        """
        response = self.client.get_vectors(
            vectorBucketName=self.vector_bucket_name,
            indexName=self.collection_name,
            keys=[vector_id],
            returnData=False,
            returnMetadata=True,
        )
        vectors = response.get("vectors", [])
        if not vectors:
            return None
        return self._parse_output(vectors)[0]

    def list_cols(self):
        """List the names of the indexes in the bucket.

        Goes in: nothing; comes out: a list of names. It only reads.
        """
        response = self.client.list_indexes(vectorBucketName=self.vector_bucket_name)
        return [idx["indexName"] for idx in response.get("indexes", [])]

    def delete_col(self):
        """Delete this store's index.

        Goes in: nothing; comes out: nothing. It removes the whole index.
        """
        self.client.delete_index(
            vectorBucketName=self.vector_bucket_name, indexName=self.collection_name
        )

    def col_info(self):
        """Describe this store's index.

        Goes in: nothing; comes out: the index description dict. It only reads.
        """
        response = self.client.get_index(
            vectorBucketName=self.vector_bucket_name, indexName=self.collection_name
        )
        return response.get("index", {})

    def list(self, filters=None, top_k=None):
        """List stored vectors' metadata, optionally limited by filters and a count.

        Goes in: filters and a top_k limit; comes out: a list of results. It only reads.
        """
        params = {
            "vectorBucketName": self.vector_bucket_name,
            "indexName": self.collection_name,
            "returnData": False,
            "returnMetadata": True,
        }
        if top_k:
            params["maxResults"] = top_k

        paginator = self.client.get_paginator("list_vectors")
        pages = paginator.paginate(**params)
        all_vectors = []
        for page in pages:
            all_vectors.extend(page.get("vectors", []))
        results = self._parse_output(all_vectors)
        if filters:
            results = [
                result
                for result in results
                if result.payload and all(result.payload.get(k) == v for k, v in filters.items())
            ]
        if top_k:
            results = results[:top_k]
        return [results]

    def reset(self):
        """Delete the index and create it again empty.

        Goes in: nothing; comes out: nothing. All vectors in the index are lost.
        """
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.create_col(self.collection_name, self.embedding_model_dims, self.distance_metric)
