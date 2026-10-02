from kicad_bundle.smoke import EXPECTED, netlist_nets

NETLIST = """(export (version "E")
  (nets
    (net (code "1") (name "/IN") (class "Default") (node (ref "R1") (pin "1") (pintype "passive")))
    (net (code "2") (name "/OUT") (class "Default")
      (node (ref "C1") (pin "2") (pinfunction "~_2") (pintype "passive"))
      (node (ref "R1") (pin "2") (pintype "passive")))
    (net (code "3") (name "GND") (class "Default") (node (ref "C1") (pin "1") (pintype "passive")))))
"""


def test_netlist_nets_reads_kicadsexpr():
    assert netlist_nets(NETLIST) == EXPECTED
