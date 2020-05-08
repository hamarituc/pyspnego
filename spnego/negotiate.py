# Copyright: (c) 2020, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import base64
import logging

from typing import (
    List,
    Optional,
    Tuple,
)

from spnego._context import (
    ContextProxy,
    ContextReq,
    GSSMech,
)

from spnego._spnego import (
    NegState,
    NegTokenInit,
    NegTokenInit2,
    NegTokenResp,
    pack_mech_type_list,
    pack_neg_token_init,
    pack_neg_token_init2,
    pack_neg_token_resp,
    unpack_neg_token,
)

from spnego._text import (
    to_text,
)

from spnego.gssapi import (
    GSSAPIProxy,
)

from spnego.ntlm import (
    NTLMProxy,
)


log = logging.getLogger(__name__)


class NegotiateProxy(ContextProxy):
    """A context wrapper for a Python managed SPNEGO context.

    This is a context that can be used on Linux to generate SPNEGO tokens based on the raw Kerberos or NTLM tokens
    generated by gssapi or our Python NTLM provider This is used as a fallback if gssapi is not available or cannot
    generate SPNEGO tokens.

    Args:
    """

    def __init__(self, username=None, password=None, hostname=None, service=None, channel_bindings=None,
                 context_req=ContextReq.default, usage='initiate', protocol='negotiate'):
        super(NegotiateProxy, self).__init__(username, password, hostname, service, channel_bindings, context_req,
                                             usage, protocol, False)

        self._hostname = hostname  # type: str
        self._service = service  # type: str
        self._complete = False  # type: bool
        self._context_list = []  # type: List[Tuple[GSSMech, ContextProxy, Optional[bytes]]]
        self._mech_list = []

        self._init_sent = False  # type: bool
        self._mech_sent = False  # type: bool
        self._mic_sent = False  # type: bool
        self._mic_recv = False  # type: bool
        self._mic_required = False  # type: bool

    @classmethod
    def available_protocols(cls, context_req=None):
        # We always support Negotiate and NTLM as we have our builtin NTLM backend and only support kerberos if gssapi
        # is present.
        protocols = [u'ntlm', u'negotiate']

        # Make sure we add Kerberos first as the order is important.
        if u'kerberos' in GSSAPIProxy.available_protocols(context_req=context_req):
            protocols.insert(0, u'kerberos')

        return protocols

    @classmethod
    def iov_available(cls):
        # NTLM does not support IOV so we can only say that IOV is available if GSSAPI IOV is available.
        return GSSAPIProxy.iov_available()

    @property
    def complete(self):
        return self._complete

    @property
    def context_attr(self):
        return self._context.context_attr

    @property
    def negotiated_protocol(self):
        return self._context.negotiated_protocol

    @property
    def session_key(self):
        return self._context.session_key

    def step(self, in_token=None):
        log.debug("SPNEGO step input: %s", to_text(base64.b64encode(in_token or b"")))

        # Step 1. Process SPNEGO mechs.
        mech_token_in, mech_list_mic = self._step_spnego_input(in_token=in_token)

        # Step 2. Process the inner context tokens.
        mech_token_out = self._step_spnego_token(in_token=mech_token_in)

        # Step 3. Process / generate the mechListMIC.
        out_mic = self._step_spnego_mic(in_mic=mech_list_mic)

        # Step 4. Generate the output SPNEGO token.
        out_token = self._step_spnego_output(out_token=mech_token_out, out_mic=out_mic)

        if self.complete:
            # Remove the leftover contexts if there are still others remaining.
            self._context_list = [self._context_list[0]]

        log.debug("SPNEGO step output: %s" % to_text(base64.b64encode(out_token or b"")))

        return out_token

    def _step_spnego_input(self, in_token=None):
        mech_list_mic = None
        token = None

        if in_token:
            in_token = unpack_neg_token(in_token)

            mech_list_mic = in_token.mech_list_mic

            if isinstance(in_token, (NegTokenInit, NegTokenInit2)):
                token = in_token.mech_token

                self._mech_list = self._rebuild_context_list(self._mech_list, in_token=token)
                self._init_sent = True
                # TODO: Determine that the mech list priority matches our priority.

            elif isinstance(in_token, NegTokenResp):
                token = in_token.response_token

                # If we have received the supported_mech then we don't need to send our own.
                if in_token.supported_mech:
                    # TODO: verify that the supported_mech is the one we originally sent.
                    self._mech_sent = True

                # Raise exception if we are rejected and have no error info (mechToken) that will give us more info.
                if in_token.neg_state == NegState.reject and not token:
                    raise Exception("Received SPNEGO rejection")

                if in_token.neg_state == NegState.request_mic:
                    self._mic_required = True
                elif in_token.neg_state == NegState.accept_complete:
                    self._complete = True

        else:
            # We are starting the process and can build our own mech list based on our own priority.
            self._mech_list = self._rebuild_context_list()

        return token, mech_list_mic

    def _step_spnego_token(self, in_token=None):
        if not self._context.complete:
            # The first round should also have the token pre-cached, retrieve that and clear out the cache nwo that we
            # no longer need it. Otherwise create the next token as necessary.
            if self._context_list[0][2]:
                out_token = self._context_list[0][2]
                self._context_list[0] = (self._context_list[0][0], self._context_list[0][1], None)

            else:
                out_token = self._context.step(in_token=in_token)

            # NTLM has a special case where we need to tell it it's ok to generate the MIC and also determine if
            # it actually did set the MIC as that controls the mechListMIC for the SPNEGO token.
            if self._requires_mech_list_mic:
                self._mic_required = True

            return out_token

    def _step_spnego_mic(self, in_mic=None):
        if in_mic:
            self.verify(pack_mech_type_list(self._mech_list), in_mic)
            self._reset_ntlm_crypto_state(outgoing=False)

            self._mic_required = True  # If we received a mechListMIC we need to send one back.
            self._mic_recv = True

            if self._mic_sent:
                self._complete = True

        if self._mic_required and not self._mic_sent:
            out_mic = self.sign(pack_mech_type_list(self._mech_list))
            self._reset_ntlm_crypto_state()

            self._mic_sent = True

            return out_mic

    def _step_spnego_output(self, out_token=None, out_mic=None):
        if not self._init_sent:
            self._init_sent = True

            build_func = pack_neg_token_init if self.usage == 'initiate' else pack_neg_token_init2
            return build_func(self._mech_list, mech_token=out_token, mech_list_mic=out_mic)

        elif not self.complete:
            # As per RFC 4178 - 4.2.2: supportedMech should only be present in the first reply from the target.
            # https://tools.ietf.org/html/rfc4178#section-4.2.2
            supported_mech = None
            if not self._mech_sent:
                supported_mech = self._mech
                self._mech_sent = True

            state = NegState.accept_incomplete

            if self._context.complete:
                if self._mic_sent and not self._mic_recv:
                    # FIXME: should only accept when usage='accept' and the preferred mech wasn't used.
                    state = NegState.request_mic
                else:
                    state = NegState.accept_complete
                    self._complete = True

            return pack_neg_token_resp(neg_state=state, response_token=out_token,
                                       supported_mech=supported_mech, mech_list_mic=out_mic)

    def wrap(self, data, encrypt=True, qop=None):
        return self._context.wrap(data, encrypt=encrypt, qop=qop)

    def wrap_iov(self, iov, encrypt=True, qop=None):
        return self._context.wrap_iov(iov, encrypt=encrypt, qop=qop)

    def unwrap(self, data):
        return self._context.unwrap(data)

    def unwrap_iov(self, iov):
        return self._context.unwrap_iov(iov)

    def sign(self, data, qop=None):
        return self._context.sign(data, qop=qop)

    def verify(self, data, mic):
        return self._context.verify(data, mic)

    @property
    def _context(self):
        if self._context_list:
            return self._context_list[0][1]

    @property
    def _context_attr_map(self):
        return []  # SPNEGO layer uses the generic commands, the underlying context has it's own specific map.

    @property
    def _requires_mech_list_mic(self):
        return self._context._requires_mech_list_mic

    def _convert_channel_bindings(self, bindings):
        return bindings  # SPNEGO layer uses the generic version, the underlying context has it's own specific way.

    def _convert_iov_buffer(self, iov):
        pass  # Handled in the underlying context.

    def _create_spn(self, service, principal):
        return u"%s@%s" % (service.lower(), principal)

    def _rebuild_context_list(self, mech_list=None, in_token=None):
        # type: (Optional[List[str]], Optional[bytes]) -> List[str]

        gssapi_protocols = [p for p in GSSAPIProxy.available_protocols(context_req=self.context_req)
                            if p != 'negotiate']
        available_mechs = [getattr(GSSMech, p) for p in gssapi_protocols]
        available_mechs.append(GSSMech.ntlm)  # We can always offer NTLM.

        chosen_mechs = []
        one_required = True
        if mech_list:
            for mech in mech_list:
                try:
                    gss_mech = GSSMech.from_oid(mech)
                except ValueError:
                    continue

                if gss_mech in available_mechs or (GSSMech.kerberos in available_mechs and gss_mech.is_kerberos_oid()):
                    chosen_mechs.append(gss_mech)

        else:
            mech_list = []
            one_required = False
            chosen_mechs = available_mechs

        if not chosen_mechs:
            raise Exception("Cannot negotiate a common mech")

        for mech in chosen_mechs:
            if mech.name in gssapi_protocols:
                try:
                    context = GSSAPIProxy(self.username, self.password, self._hostname, self._service,
                                          self._channel_bindings, self.context_req, self.usage, protocol=mech.name,
                                          is_wrapped=True)
                    first_token = context.step(in_token=in_token)
                except Exception as e:
                    log.debug("Failed to create gssapi context for SPNEGO protocol %s: %s", mech.name, str(e))
                    continue

            else:
                context = NTLMProxy(self.username, self.password, self._hostname, self._service,
                                    self._channel_bindings, self.context_req, self.usage, is_wrapped=True)
                first_token = context.step(in_token=in_token)

            # We were able to build the context, add it to the list.
            self._context_list.append((mech, context, first_token))

            if one_required:
                break
            else:
                mech_list.append(mech.value)

        return mech_list

    def _reset_ntlm_crypto_state(self, outgoing=True):
        return self._context._reset_ntlm_crypto_state(outgoing=outgoing)
