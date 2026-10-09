# ADR-0005: Contrast fixes

`--faint` is 1.6:1 on paper, below WCAG 1.4.11's 3:1 for control boundaries. Empty controls
(unchecked split boxes, empty OTP boxes, empty inputs) use `--control-edge`, equal to `--muted`
(6.8:1). The violet member color changes from #7B61FF (4.2:1 with white) to #6A4CFF (5.1:1); its
initial is white, all other member colors use #121210. Dark `--shadow` stays #7B61FF (not text).
A unit test checks every text/background token pair.
