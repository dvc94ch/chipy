import chipy.Chipy as chipy
import eispice
import json


class EispiceDigitalInput(eispice.PyB):
    def __init__(self, pNode, high, low):
        eispice.PyB.__init__(self, pNode, eispice.GND, eispice.Voltage,
                             self.v(pNode), eispice.Time)
        self.value = False
        self.high = high
        self.low = low

    def model(self, vp, time):
        # Translate binary value to voltage level
        return self.high if self.value else self.low

    def set_value(self, value):
        self.value = value


class ChipyAnalogModule:
    def __init__(self, name):
        self.name = name
        self.signals = dict()
        self.devices = dict()
        self.codeloc = chipy.ChipyCodeLoc()

        assert name not in chipy.ChipyModulesDict
        chipy.ChipyModulesDict[name] = self

    def __enter__(self):
        chipy.ChipyContext(newmod=self)

    def __exit__(self, type, value, traceback):
        chipy.ChipyCurrentContext.popctx()

    def write_verilog(self, f):
        '''Mock write verilog for chipy compatibility.'''
        pass

    def netlist(self):
        def yosys_cell(skin_name, pin_net_dict, value=None):
            connections = {}
            for pin, net in pin_net_dict.items():
                connections[pin] = [net]

            cell = {
                'type': skin_name,
                'connections': connections,
            }

            if value is not None:
                cell['attributes'] = {
                    'value': value
                }
            return cell

        ports, cells = {}, {}
        for ref, device in self.devices.items():
            if device.Skin is not None:
                cells[ref] = yosys_cell(device.Skin, device.connections(),
                                        device.value)
        for name, signal in self.signals.items():
            if signal.power:
                cells[name] = yosys_cell('power', {'VCC': signal.id}, name)
            elif signal.ground:
                cells[name] = yosys_cell('ground', {'GND': signal.id}, name)
            elif signal.inport:
                ports[name] = {'direction': 'input', 'bits': [signal.id]}
            elif signal.outport:
                ports[name] = {'direction': 'output', 'bits': [signal.id]}

        return {
            'ports': ports,
            'cells': cells,
        }

    def eispice_model(self):
        cct = eispice.Circuit(self.name)
        for name, device in self.devices.items():
            cct.__setattr__(name, device.eispice_model())
        for name, signal in self.signals.items():
            if signal.digital and signal.inport:
                model = EispiceDigitalInput(signal.name, signal.high_value,
                                            signal.low_value)
                cct.__setattr__(name, model)
        return cct


class ChipyAnalogSignal:
    SigId = 0

    def __init__(self, module, name=None):
        if name is None:
            name = chipy.ChipyAutoName()

        module.signals[name] = self

        type(self).SigId += 1
        self.id = self.SigId
        self.name = name
        self.module = module
        self.codeloc = chipy.ChipyCodeLoc()
        self.width = 1
        self.power = False
        self.power_value = 0
        self.ground = False
        self.inport = False
        self.outport = False
        self.digital = False
        self.high_value = 0
        self.low_value = 0
        self.high_threshold = 0
        self.low_threshold = 0

    def eispice_model(self):
        if self.ground:
            return eispice.GND
        else:
            return self.name


def AnalogSig(arg, width=None):
    if isinstance(arg, ChipyAnalogSignal):
        if width is not None:
            assert arg.width == width
        return arg

    if isinstance(arg, (tuple, list)):
        assert width is None
        return chipy.Concat(arg)

    if isinstance(arg, str):
        module = chipy.ChipyCurrentContext.module
        if arg in module.signals:
            signal = module.signals[arg]
            if width is not None:
                assert signal.width == width
            return signal
        else:
            signal = ChipyAnalogSignal(module, arg)
            if width is not None:
                signal.width = width
            return signal

    assert 0


class ChipyDevice:
    IdCount = None
    RefDes = None
    EispiceModel = None
    Skin = None
    value = ' '

    def __init__(self, module, name=None):
        if name is None:
            type(self).IdCount += 1
            name = self.RefDes + str(self.IdCount)
        assert not name in module.devices
        module.devices[name] = self

        self.name = name
        self.module = module


class ChipyPassive(ChipyDevice):
    def __init__(self, module, name, sig1, sig2, value):
        super().__init__(module, name)
        self.sig1 = AnalogSig(sig1, 1)
        self.sig2 = AnalogSig(sig2, 1)
        self.value = value

    def eispice_model(self):
        return type(self).EispiceModel(self.sig1.eispice_model(),
                                       self.sig2.eispice_model(),
                                       self.value)

    def connections(self):
        return {'L': self.sig1.id, 'R': self.sig2.id}


class ChipyResistor(ChipyPassive):
    IdCount = 0
    RefDes = 'R'
    EispiceModel = eispice.R
    Skin = 'resistor'


class ChipyCapacitor(ChipyPassive):
    IdCount = 0
    RefDes = 'C'
    EispiceModel = eispice.C
    Skin = 'capacitor'


class ChipyTransistor(ChipyDevice):
    IdCount = 0
    RefDes = 'Q'
    EispiceModel = eispice.Q
    Skin = 'transistor-npn'

    def __init__(self, module, name, c, b, e, substrate='0', **model):
        super().__init__(module, name)
        self.c = AnalogSig(c, 1)
        self.b = AnalogSig(b, 1)
        self.e = AnalogSig(e, 1)
        self.substrate = AnalogSig(substrate, 1)
        self.model = model

    def eispice_model(self):
        return type(self).EispiceModel(self.c.eispice_model(),
                                       self.b.eispice_model(),
                                       self.e.eispice_model(),
                                       **self.model)

    def connections(self):
        return {'C': self.c.id, 'B': self.b.id, 'E': self.e.id}


class ChipyVoltageSource(ChipyPassive):
    IdCount = 0
    RefDes = 'V'
    EispiceModel = eispice.V


def AddAnalogModule(name):
    return ChipyAnalogModule(name)


def AddDigitalInput(name, type=1, high_value=3.3, low_value=0):
    names = name.split()
    if len(names) > 1:
        return [AddDigitalInput(n) for n in names]
    assert len(names) == 1
    name = names[0]

    if not isinstance(type, int):
        raise NotImplementedError("Ports not supported yet")

    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module

    signal = ChipyAnalogSignal(module, name)
    signal.width = type
    signal.inport = True
    signal.digital = True
    signal.high_value = high_value
    signal.low_value = low_value
    return signal


def AddDigitalOutput(name, type=1):
    raise NotImplementedError("Digital outputs are not supported yet")


def AddAnalogInput(name, type=1):
    raise NotImplementedError("Analog inputs are not supported yet")


def AddAnalogOutput(name, type=1):
    names = name.split()
    if len(names) > 1:
        return [AddAnalogOutput(n) for n in names]
    assert len(names) == 1
    name = names[0]

    if not isinstance(type, int):
        raise NotImplementedError("Ports not supported yet")

    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module

    signal = ChipyAnalogSignal(module, name)
    signal.width = type
    signal.outport = True
    return signal


def AddPower(name, value):
    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module
    vs = ChipyVoltageSource(module, name, name, '0', value)
    vs.sig1.power = True
    vs.sig1.power_value = value
    return vs.sig1


def AddGnd(name):
    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module
    sig = AnalogSig(name, 1)
    sig.ground = True
    return sig


def AddR(sig1, sig2, value):
    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module
    ChipyResistor(module, None, sig1, sig2, value)


def AddC(sig1, sig2, value):
    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module
    ChipyCapacitor(module, None, sig1, sig2, value)


def AddQ(c, b, e, substrate='0', **model):
    assert chipy.ChipyCurrentContext is not None
    module = chipy.ChipyCurrentContext.module
    ChipyTransistor(module, None, c, b, e, substrate, **model)


def WriteNetlist(f):
    modules = {}
    for modname, module in chipy.ChipyModulesDict.items():
        if isinstance(module, ChipyAnalogModule):
            modules[modname] = module.netlist()
    j = json.dumps({'modules': modules}, sort_keys=True, indent=2,
                      separators=(',', ': '))
    print(j, file=f)


def EispiceModel(modname):
    return chipy.ChipyModulesDict[modname].eispice_model()
