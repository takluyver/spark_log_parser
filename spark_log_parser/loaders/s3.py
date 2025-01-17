import abc
import boto3

from pathlib import Path
from urllib.parse import ParseResult, urlparse

from botocore.client import BaseClient
from botocore.response import StreamingBody

from spark_log_parser.loaders import AbstractFileDataLoader, BlobFileReaderMixin, LinesFileReaderMixin, \
    FileChunkStreamWrapper

# boto3 clients are threadsafe, so we can use a singleton for all instances
S3_CLIENT = boto3.client("s3")


class AbstractS3FileDataLoader(AbstractFileDataLoader, abc.ABC):
    """
    Abstract class that supports loading files directly from S3
    """

    _STREAM_CHUNK_SIZE = 1024 * 1024  # 1MB

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._s3: BaseClient = S3_CLIENT

    @property
    def s3(self):
        return self._s3

    def load_item(self, filepath):
        """

        """
        parsed_url: ParseResult = filepath if isinstance(filepath, ParseResult) else urlparse(filepath)
        bucket = parsed_url.netloc
        key = parsed_url.path.lstrip('/')

        object_list = self.s3.list_objects_v2(Bucket=bucket, Prefix=key)

        contents_to_fetch = [content for content in object_list.get("Contents") if
                             not self.should_skip_file(content["Key"])]
        if not contents_to_fetch:
            raise AssertionError(f"No valid objects matching '{key}' in bucket: {bucket}")

        # TODO - define struct for these limits/thresholds
        if object_list.get("IsTruncated", False) or len(contents_to_fetch) > 100:
            raise AssertionError(f"Too many objects in bucket: {bucket}.")

        total_size = 0
        for content in contents_to_fetch:
            total_size += content["Size"]
            if total_size > 20000000000:
                raise AssertionError(f"Size limit exceeded while downloading from {filepath}.")

        responses: list[StreamingBody] = []
        file_streams: list[FileChunkStreamWrapper] = []
        # TODO - this serially fetches all matching objects in the bucket at the moment...
        #  There is likely a better way to do this? It may require some ThreadExecutors, though
        for content in contents_to_fetch:
            data: StreamingBody = self.s3.get_object(Bucket=bucket, Key=content["Key"])["Body"]
            responses.append(data)

            # Wrap the botocore.response.StreamingBody and return that so that subsequent extraction can operate on the
            #  stream vs. loading all the files into memory
            wrapped = FileChunkStreamWrapper(data.iter_chunks(self._STREAM_CHUNK_SIZE))
            file_streams.append(wrapped)

        try:
            for (content, filestream) in zip(contents_to_fetch, file_streams):
                yield from self.extract(Path(content["Key"]), filestream)
        finally:
            for data in responses:
                data.close()


class S3FileBlobDataLoader(BlobFileReaderMixin, AbstractS3FileDataLoader):
    """
    Simple HTTP loader that returns the full file as a blob of data.
    """


class S3FileLinesDataLoader(LinesFileReaderMixin, AbstractS3FileDataLoader):
    """
    Simple HTTP loader that returns the file as a stream of lines (delimited by `\n`).
    """
