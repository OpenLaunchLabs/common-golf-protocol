"""The only aioquic private-API adapter; pinned and covered by live TLS tests.

The public aioquic API lacks client-certificate request and stream-lifetime
accessors. Keep these version-sensitive dependencies in this module.
"""
from importlib.metadata import version

from aioquic.quic.events import StopSendingReceived


class AioquicCompatibilityError(RuntimeError):
    pass


def configure_connection(quic):
    if version("aioquic") != "1.2.0":
        raise AioquicCompatibilityError("CGP's aioquic adapter is tested with aioquic==1.2.0")
    try:
        original = quic._initialize
        quic._streams
        handlers = quic._QuicConnection__frame_handlers
        original_stop, stop_epochs = handlers[0x05]
    except AttributeError as exc:
        raise AioquicCompatibilityError("aioquic internal APIs changed") from exc
    def initialize(peer_cid):
        original(peer_cid)
        if not quic.configuration.is_client:
            quic.tls._request_client_certificate = True
    quic._initialize = initialize

    def stop_sending(context, frame_type, buffer):
        position = buffer.tell()
        stream_id = buffer.pull_uint_var()
        error_code = buffer.pull_uint_var()
        stream = quic._streams.get(stream_id)
        if stream is None or stream.sender._reset_error_code is None:
            buffer.seek(position)
            return original_stop(context, frame_type, buffer)
        # A retransmitted STOP_SENDING, or one crossing our own RESET_STREAM,
        # must not reset the same sending half twice. aioquic 1.2.0 asserts in
        # that case. Keep all direction/retirement checks and application events;
        # only the already-completed reset operation is omitted.
        quic._assert_stream_can_send(frame_type, stream_id)
        quic._get_or_create_stream(frame_type, stream_id)
        if quic._quic_logger is not None:
            context.quic_logger_frames.append(quic._quic_logger.encode_stop_sending_frame(
                error_code=error_code, stream_id=stream_id))
        quic._events.append(StopSendingReceived(error_code=error_code, stream_id=stream_id))

    handlers[0x05] = (stop_sending, stop_epochs)


def peer_certificate(quic):
    return quic.tls._peer_certificate


def stream_finished(quic, stream_id):
    stream = quic._streams.get(stream_id)
    if stream is not None and stream_id & 2:
        local_initiator = not bool(stream_id & 1) if quic.configuration.is_client else bool(stream_id & 1)
        if local_initiator:
            # aioquic 1.2.0 ignores QuicStreamReceiver(readable=False), leaving
            # this impossible receiving half unfinished forever. Normalize it
            # here so ACKed send-only streams retire in QUIC and SDK state.
            stream.receiver.is_finished = True
    return stream is None or stream.is_finished


def queued_stream_bytes(quic):
    return sum(len(stream.sender._buffer) for stream in quic._streams.values())
