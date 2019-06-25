from abc import abstractproperty

from ..hdl import *
from ..build import *


__all__ = ["LatticeECP5Platform"]


class LatticeECP5Platform(TemplatedPlatform):
    """
    Required tools:
        * ``yosys``
        * ``nextpnr-ecp5``
        * ``ecppack``

    Available overrides:
        * ``verbose``: enables logging of informational messages to standard error.
        * ``read_verilog_opts``: adds options for ``read_verilog`` Yosys command.
        * ``synth_opts``: adds options for ``synth_ecp5`` Yosys command.
        * ``script_after_read``: inserts commands after ``read_ilang`` in Yosys script.
        * ``script_after_synth``: inserts commands after ``synth_ecp5`` in Yosys script.
        * ``yosys_opts``: adds extra options for Yosys.
        * ``nextpnr_opts``: adds extra options for nextpnr.
        * ``ecppack_opts``: adds extra options for ecppack.

    Build products:
        * ``{{name}}.rpt``: Yosys log.
        * ``{{name}}.json``: synthesized RTL.
        * ``{{name}}.tim``: nextpnr log.
        * ``{{name}}.config``: ASCII bitstream.
        * ``{{name}}.bit``: binary bitstream.
        * ``{{name}}.svf``: JTAG programming vector.
    """

    device  = abstractproperty()
    package = abstractproperty()
    speed   = abstractproperty()

    _nextpnr_device_options = {
        "LFE5U-12F":    "--25k",
        "LFE5U-25F":    "--25k",
        "LFE5U-45F":    "--45k",
        "LFE5U-85F":    "--85k",
        "LFE5UM-12F":   "--um-25k",
        "LFE5UM-25F":   "--um-25k",
        "LFE5UM-45F":   "--um-45k",
        "LFE5UM-85F":   "--um-85k",
        "LFE5UM5G-12F": "--um5g-25k",
        "LFE5UM5G-25F": "--um5g-25k",
        "LFE5UM5G-45F": "--um5g-45k",
        "LFE5UM5G-85F": "--um5g-85k",
    }
    _nextpnr_package_options = {
        "BG256": "caBGA256",
        "MG285": "csfBGA285",
        "BG381": "caBGA381",
        "BG554": "caBGA554",
        "BG756": "caBGA756",
    }

    file_templates = {
        **TemplatedPlatform.build_script_templates,
        "{{name}}.il": r"""
            # {{autogenerated}}
            {{emit_design("rtlil")}}
        """,
        "{{name}}.ys": r"""
            # {{autogenerated}}
            {% for file in platform.extra_files %}
                {% if file.endswith(".v") -%}
                    read_verilog {{get_override("read_opts")|join(" ")}} {{file}}
                {% elif file.endswith(".sv") -%}
                    read_verilog -sv {{get_override("read_opts")|join(" ")}} {{file}}
                {% endif %}
            {% endfor %}
            read_ilang {{name}}.il
            {{get_override("script_after_read")|default("# (script_after_read placeholder)")}}
            synth_ecp5 {{get_override("synth_opts")|join(" ")}} -top {{name}}
            {{get_override("script_after_synth")|default("# (script_after_synth placeholder)")}}
            write_json {{name}}.json
        """,
        "{{name}}.lpf": r"""
            # {{autogenerated}}
            BLOCK ASYNCPATHS;
            BLOCK RESETPATHS;
            {% for port_name, pin_name, extras in platform.iter_port_constraints_bits() -%}
                LOCATE COMP "{{port_name}}" SITE "{{pin_name}}";
                IOBUF PORT "{{port_name}}"
                    {%- for key, value in extras.items() %} {{key}}={{value}}{% endfor %};
            {% endfor %}
            {% for signal, frequency in platform.iter_clock_constraints() -%}
                FREQUENCY PORT "{{signal.name}}" {{frequency}} HZ;
            {% endfor %}
        """
    }
    command_templates = [
        r"""
        {{get_tool("yosys")}}
            {{quiet("-q")}}
            {{get_override("yosys_opts")|join(" ")}}
            -l {{name}}.rpt
            {{name}}.ys
        """,
        r"""
        {{get_tool("nextpnr-ecp5")}}
            {{quiet("--quiet")}}
            {{get_override("nextpnr_opts")|join(" ")}}
            --log {{name}}.tim
            {{platform._nextpnr_device_options[platform.device]}}
            --package {{platform._nextpnr_package_options[platform.package]|upper}}
            --speed {{platform.speed}}
            --json {{name}}.json
            --lpf {{name}}.lpf
            --textcfg {{name}}.config
        """,
        r"""
        {{get_tool("ecppack")}}
            {{verbose("--verbose")}}
            --input {{name}}.config
            --bit {{name}}.bit
            --svf {{name}}.svf
        """
    ]

    _single_ended_io_types = [
        "HSUL12", "LVCMOS12", "LVCMOS15", "LVCMOS18", "LVCMOS25", "LVCMOS33", "LVTTL33",
        "SSTL135_I", "SSTL135_II", "SSTL15_I", "SSTL15_II", "SSTL18_I", "SSTL18_II",
    ]
    _differential_io_types = [
        "BLVDS25", "BLVDS25E", "HSUL12D", "LVCMOS18D", "LVCMOS25D", "LVCMOS33D",
        "LVDS", "LVDS25E", "LVPECL33", "LVPECL33E", "LVTTL33D", "MLVDS", "MLVDS25E",
        "SLVS", "SSTL135D_II", "SSTL15D_II", "SSTL18D_II", "SUBLVDS",
    ]

    def should_skip_port_component(self, port, attrs, component):
        # On ECP5, a differential IO is placed by only instantiating an IO buffer primitive at
        # the PIOA or PIOC location, which is always the non-inverting pin.
        if attrs.get("IO_TYPE", "LVCMOS25") in self._differential_io_types and component == "n":
            return True
        return False

    def _get_xdr_buffer(self, m, pin, i_invert=None, o_invert=None):
        def get_ireg(clk, d, q):
            for bit in range(len(q)):
                m.submodules += Instance("IFS1P3DX",
                    i_SCLK=clk,
                    i_SP=Const(1),
                    i_CD=Const(0),
                    i_D=d[bit],
                    o_Q=q[bit]
                )

        def get_oreg(clk, d, q):
            for bit in range(len(q)):
                m.submodules += Instance("OFS1P3DX",
                    i_SCLK=clk,
                    i_SP=Const(1),
                    i_CD=Const(0),
                    i_D=d[bit],
                    o_Q=q[bit]
                )

        def get_iddr(sclk, d, q0, q1):
            for bit in range(len(d)):
                m.submodules += Instance("IDDRX1F",
                    i_SCLK=sclk,
                    i_RST=Const(0),
                    i_D=d[bit],
                    o_Q0=q0[bit], o_Q1=q1[bit]
                )

        def get_oddr(sclk, d0, d1, q):
            for bit in range(len(q)):
                m.submodules += Instance("ODDRX1F",
                    i_SCLK=sclk,
                    i_RST=Const(0),
                    i_D0=d0[bit], i_D1=d1[bit],
                    o_Q=q[bit]
                )

        def get_ixor(z, invert):
            if invert is None:
                return z
            else:
                a = Signal.like(z, name_suffix="_x{}".format(1 if invert else 0))
                for bit in range(len(z)):
                    m.submodules += Instance("LUT4",
                        p_INIT=0x5555 if invert else 0xaaaa,
                        i_A=a[bit],
                        o_Z=z[bit]
                    )
                return a

        def get_oxor(a, invert):
            if invert is None:
                return a
            else:
                z = Signal.like(a, name_suffix="_x{}".format(1 if invert else 0))
                for bit in range(len(a)):
                    m.submodules += Instance("LUT4",
                        p_INIT=0x5555 if invert else 0xaaaa,
                        i_A=a[bit],
                        o_Z=z[bit]
                    )
                return z

        if "i" in pin.dir:
            if pin.xdr < 2:
                pin_i  = get_ixor(pin.i,  i_invert)
            elif pin.xdr == 2:
                pin_i0 = get_ixor(pin.i0, i_invert)
                pin_i1 = get_ixor(pin.i1, i_invert)
        if "o" in pin.dir:
            if pin.xdr < 2:
                pin_o  = get_oxor(pin.o,  o_invert)
            elif pin.xdr == 2:
                pin_o0 = get_oxor(pin.o0, o_invert)
                pin_o1 = get_oxor(pin.o1, o_invert)

        i = o = t = None
        if "i" in pin.dir:
            i = Signal(pin.width, name="{}_xdr_i".format(pin.name))
        if "o" in pin.dir:
            o = Signal(pin.width, name="{}_xdr_o".format(pin.name))
        if pin.dir in ("oe", "io"):
            t = Signal(1,         name="{}_xdr_t".format(pin.name))

        if pin.xdr == 0:
            if "i" in pin.dir:
                i = pin_i
            if "o" in pin.dir:
                o = pin_o
            if pin.dir in ("oe", "io"):
                t = ~pin_oe
        elif pin.xdr == 1:
            # Note that currently nextpnr will not pack an FF (*FS1P3DX) into the PIO.
            if "i" in pin.dir:
                get_ireg(pin.i_clk, i, pin_i)
            if "o" in pin.dir:
                get_oreg(pin.o_clk, pin_o, o)
            if pin.dir in ("oe", "io"):
                get_oreg(pin.o_clk, ~pin.oe, t)
        elif pin.xdr == 2:
            if "i" in pin.dir:
                get_iddr(pin.i_clk, i, pin_i0, pin_i1)
            if "o" in pin.dir:
                get_oddr(pin.o_clk, pin_o0, pin_o1, o)
            if pin.dir in ("oe", "io"):
                # It looks like Diamond will not pack an OREG as a tristate register in a DDR PIO.
                # It is not clear what is the recommended set of primitives for this task.
                # Similarly, nextpnr will not pack anything as a tristate register in a DDR PIO.
                get_oreg(pin.o_clk, ~pin.oe, t)
        else:
            assert False

        return (i, o, t)

    def get_input(self, pin, port, attrs, invert):
        self._check_feature("single-ended input", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        t, o, t = self._get_xdr_buffer(m, pin, i_invert=True if invert else None)
        for bit in range(len(port)):
            m.submodules += Instance("IB",
                i_I=port[bit],
                o_O=i[bit]
            )
        return m

    def get_output(self, pin, port, attrs, invert):
        self._check_feature("single-ended output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, o_invert=True if invert else None)
        for bit in range(len(port)):
            m.submodules += Instance("OB",
                i_I=o[bit],
                o_O=port[bit]
            )
        return m

    def get_tristate(self, pin, port, attrs, invert):
        self._check_feature("single-ended tristate", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, o_invert=True if invert else None)
        for bit in range(len(port)):
            m.submodules += Instance("OBZ",
                i_T=t,
                i_I=o[bit],
                o_O=port[bit]
            )
        return m

    def get_input_output(self, pin, port, attrs, invert):
        self._check_feature("single-ended input/output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, i_invert=True if invert else None,
                                               o_invert=True if invert else None)
        for bit in range(len(port)):
            m.submodules += Instance("BB",
                i_T=t,
                i_I=o[bit],
                o_O=i[bit],
                io_B=port[bit]
            )
        return m

    def get_diff_input(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential input", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, i_invert=True if invert else None)
        for bit in range(len(p_port)):
            m.submodules += Instance("IB",
                i_I=p_port[bit],
                o_O=i[bit]
            )
        return m

    def get_diff_output(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, o_invert=True if invert else None)
        for bit in range(len(p_port)):
            m.submodules += Instance("OB",
                i_I=o[bit],
                o_O=p_port[bit],
            )
        return m

    def get_diff_tristate(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential tristate", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, o_invert=True if invert else None)
        for bit in range(len(p_port)):
            m.submodules += Instance("OBZ",
                i_T=t,
                i_I=o[bit],
                o_O=p_port[bit],
            )
        return m

    def get_diff_input_output(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential input/output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        i, o, t = self._get_xdr_buffer(m, pin, i_invert=True if invert else None,
                                               o_invert=True if invert else None)
        for bit in range(len(p_port)):
            m.submodules += Instance("BB",
                i_T=t,
                i_I=o[bit],
                o_O=i[bit],
                io_B=p_port[bit],
            )
        return m
