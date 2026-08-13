"""ext_proc PDP: continue unless MCP tools/call fails the Quality Agreement."""

from __future__ import annotations

from collections.abc import Iterator

import grpc

from countersign_tqa import external_processor_pb2 as pb2
from countersign_tqa import external_processor_pb2_grpc as pb2_grpc
from countersign_tqa.mcp_payload import parse_mcp_tool_call
from countersign_tqa.policy import authorize_department_read

HTTP_FORBIDDEN = 403


def continue_response(request: pb2.ProcessingRequest) -> pb2.ProcessingResponse:
    """Return the matching empty continue message for each ext_proc event."""
    if request.HasField("request_headers"):
        return pb2.ProcessingResponse(request_headers=pb2.HeadersResponse())
    if request.HasField("response_headers"):
        return pb2.ProcessingResponse(response_headers=pb2.HeadersResponse())
    if request.HasField("request_body"):
        return pb2.ProcessingResponse(request_body=pb2.BodyResponse())
    if request.HasField("response_body"):
        return pb2.ProcessingResponse(response_body=pb2.BodyResponse())
    if request.HasField("request_trailers"):
        return pb2.ProcessingResponse(request_trailers=pb2.TrailersResponse())
    if request.HasField("response_trailers"):
        return pb2.ProcessingResponse(response_trailers=pb2.TrailersResponse())
    return pb2.ProcessingResponse()


def deny_response(reason: str) -> pb2.ProcessingResponse:
    details = reason if reason.startswith("tqa_") else f"tqa_{reason}"
    return pb2.ProcessingResponse(
        immediate_response=pb2.ImmediateResponse(
            status=pb2.HttpStatus(code=HTTP_FORBIDDEN),
            body=details.encode("utf-8"),
            details=details,
        )
    )


def decide_request_body(body: bytes) -> pb2.ProcessingResponse:
    call = parse_mcp_tool_call(body)
    if call is None:
        return pb2.ProcessingResponse(request_body=pb2.BodyResponse())
    decision = authorize_department_read(call)
    if decision.allow:
        return pb2.ProcessingResponse(request_body=pb2.BodyResponse())
        return pb2.ProcessingResponse(request_body=pb2.BodyResponse())
    return deny_response(decision.details)


class TqaProcessor(pb2_grpc.ExternalProcessorServicer):
    def Process(
        self,
        request_iterator: Iterator[pb2.ProcessingRequest],
        context: grpc.ServicerContext,
    ) -> Iterator[pb2.ProcessingResponse]:
        chunks = bytearray()
        for request in request_iterator:
            if request.observability_mode:
                continue
            if request.HasField("request_body"):
                chunks.extend(request.request_body.body)
                if not request.request_body.end_of_stream:
                    yield pb2.ProcessingResponse(request_body=pb2.BodyResponse())
                    continue
                yield decide_request_body(bytes(chunks))
                chunks.clear()
                continue
            yield continue_response(request)


# Spike name kept so existing tests and imports keep working.
ContinueProcessor = TqaProcessor
