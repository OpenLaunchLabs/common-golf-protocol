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
        if quic.configuration.alpn_protocols == ["common-golf/draft-3"]:
            _configure_raw_public_keys(quic.tls, quic.configuration.is_client)
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
    quic._cgp_pending_menu = (quic.configuration.is_client and
                             quic.configuration.alpn_protocols == ["common-golf/draft-3"])
    quic._cgp_pending_streams = set()
    if quic.configuration.alpn_protocols == ["common-golf/draft-3"]:
        _configure_datagram_queue(quic)
    connection_limits = quic._write_connection_limits
    stream_limits = quic._write_stream_limits

    def write_connection_limits(*args, **kwargs):
        # Do not return credit for unread pre-menu data. The initial QUIC
        # connection window bounds all pending bytes while info can overtake it.
        limits = (quic._local_max_data, quic._local_max_streams_bidi, quic._local_max_streams_uni)
        used = [limit.used for limit in limits]
        if quic._cgp_pending_menu:
            for limit in limits:
                limit.used = 0
        try:
            return connection_limits(*args, **kwargs)
        finally:
            for limit, previous in zip(limits, used):
                limit.used = previous

    def write_stream_limits(builder, space, stream):
        highest = stream.receiver.highest_offset
        if stream.stream_id in quic._cgp_pending_streams:
            stream.receiver.highest_offset = 0
        try:
            return stream_limits(builder, space, stream)
        finally:
            stream.receiver.highest_offset = highest
    quic._write_connection_limits = write_connection_limits
    quic._write_stream_limits = write_stream_limits


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


class RawPublicKey:
    """Certificate-shaped adapter; its wire bytes are DER SubjectPublicKeyInfo."""
    def __init__(self, key):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Ed25519 raw public key required")
        self.key = key

    def public_key(self):
        return self.key

    def public_bytes(self, encoding):
        from cryptography.hazmat.primitives.serialization import PublicFormat
        return self.key.public_bytes(encoding, PublicFormat.SubjectPublicKeyInfo)


def _configure_raw_public_keys(context, is_client):
    """RFC 7250 over TLS 1.3, confined to one pinned aioquic TLS context.

    CertificateVerify remains aioquic's normal transcript signature check.
    RawPublicKey must be selected in both directions; there is no X.509 fallback.
    """
    from aioquic import tls
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    certificate_types = (19, 20)  # client_certificate_type, server_certificate_type
    context.certificate = RawPublicKey(context.certificate_private_key.public_key())
    context.certificate_chain = []
    context._server_name = None
    context._signature_algorithms = [tls.SignatureAlgorithm.ED25519]
    context.handshake_extensions += [(kind, b"\x01\x02" if is_client else b"\x02")
                                     for kind in certificate_types]
    # Resumption cannot skip either endpoint's proof of possession.
    context.session_ticket = None
    context.get_session_ticket_cb = None
    context.new_session_ticket_cb = None

    def require_types(extensions, offered):
        for kind in certificate_types:
            values = [value for type_, value in extensions if type_ == kind]
            if len(values) != 1:
                raise tls.AlertHandshakeFailure("both raw public key certificate types required")
            value = values[0]
            valid = (len(value) >= 2 and value[0] == len(value) - 1 and 2 in value[1:]) if offered else value == b"\x02"
            if not valid:
                raise tls.AlertHandshakeFailure("RawPublicKey must be negotiated in both directions")

    if is_client:
        original_extensions = context._client_handle_encrypted_extensions
        def extensions(buffer):
            position = buffer.tell()
            received = tls.pull_encrypted_extensions(buffer)
            buffer.seek(position)
            require_types(received.other_extensions, False)
            original_extensions(buffer)
        context._client_handle_encrypted_extensions = extensions
        original_finished = context._client_handle_finished
        def finished(*args):
            if context._certificate_request is None:
                raise tls.AlertHandshakeFailure("server did not request client raw public key")
            return original_finished(*args)
        context._client_handle_finished = finished
    else:
        original_hello = context._server_handle_hello
        def hello(buffer, *args):
            position = buffer.tell()
            received = tls.pull_client_hello(buffer)
            buffer.seek(position)
            require_types(received.other_extensions, True)
            original_hello(buffer, *args)
        context._server_handle_hello = hello
        original_certificate = context._server_handle_certificate
        def certificate(buffer, *args):
            position = buffer.tell()
            received = tls.pull_certificate(buffer)
            buffer.seek(position)
            if len(received.certificates) != 1:
                raise tls.AlertBadCertificate("one client raw public key required")
            return original_certificate(buffer, *args)
        context._server_handle_certificate = certificate

    def set_peer(certificate):
        if len(certificate.certificates) != 1:
            raise tls.AlertBadCertificate("one raw public key required")
        try:
            context._peer_certificate = RawPublicKey(load_der_public_key(certificate.certificates[0][0]))
            context._peer_certificate_chain = []
        except (ValueError, TypeError) as error:
            raise tls.AlertBadCertificate("invalid Ed25519 SubjectPublicKeyInfo") from error
    context._set_peer_certificate = set_peer


def datagram_limit(quic):
    remote = quic._remote_max_datagram_frame_size or 0
    return min(remote - 3, quic._max_datagram_size - 64)


def remote_supports_datagrams(quic):
    return (quic._remote_max_datagram_frame_size or 0) >= 1200


def pending_menu_stream(quic, stream_id):
    quic._cgp_pending_streams.add(stream_id)


def release_menu_streams(quic):
    quic._cgp_pending_menu = False
    quic._cgp_pending_streams.clear()


def _configure_datagram_queue(quic):
    from aioquic.quic.packet_builder import QuicPacketBuilderStop
    from .contracts import DatagramQueue

    quic._datagrams_pending = DatagramQueue()
    original = quic._write_datagram_frame
    last_datagram_packet = None

    def write_datagram_frame(builder, data, frame_type):
        nonlocal last_datagram_packet
        # aioquic emits datagrams before stream frames. Reserve alternate
        # packets for sendable reliable traffic, and admit at most one datagram
        # in each shared packet. Its existing stream scheduler remains intact.
        reliable = any(not stream.is_finished and (
            stream.receiver.stop_pending or stream.sender.reset_pending or
            (not stream.is_blocked and not stream.sender.buffer_is_empty and
             ((stream.sender._pending_eof and len(stream.sender._pending) == 0) or stream.sender.next_offset < min(
                 stream.max_stream_data_remote, stream.sender.highest_offset +
                 quic._remote_max_data - quic._remote_max_data_used))))
            for stream in quic._streams_queue)
        if reliable and (builder.packet_number % 2 == 0 or last_datagram_packet == builder.packet_number):
            raise QuicPacketBuilderStop
        result = original(builder=builder, data=data, frame_type=frame_type)
        last_datagram_packet = builder.packet_number
        return result

    quic._write_datagram_frame = write_datagram_frame


def check_datagram_capacity(quic, channel):
    quic._datagrams_pending.check_capacity(channel)
