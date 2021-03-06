#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# DH
# V0.6  20170526

import abc

import netaddr
from neutron_lib.api import validators
from neutron_lib import constants
from oslo_log import log
from oslo_utils import netutils
from oslo_utils import uuidutils
import six

from neutron._i18n import _
from neutron.common import utils as common_utils
from neutron.ipam import exceptions as ipam_exc

# DMH
from inspect import getmembers
from pprint import pprint

LOG = log.getLogger(__name__)

@six.add_metaclass(abc.ABCMeta)
class SubnetPool(object):
    """Represents a pool of IPs available inside an address scope."""


@six.add_metaclass(abc.ABCMeta)
class SubnetRequest(object):
    """Carries the data needed to make a subnet request

    The data validated and carried by an instance of this class is the data
    that is common to any type of request.  This class shouldn't be
    instantiated on its own.  Rather, a subclass of this class should be used.
    """
    def __init__(self, tenant_id, subnet_id,
                 gateway_ip=None, allocation_pools=None):
        """Initialize and validate

        :param tenant_id: The tenant id who will own the subnet
        :type tenant_id: str uuid
        :param subnet_id: Neutron's subnet ID
        :type subnet_id: str uuid
        :param gateway_ip: An IP to reserve for the subnet gateway.
        :type gateway_ip: None or convertible to netaddr.IPAddress
        :param allocation_pools: The pool from which IPAM should allocate
            addresses.   The allocator *may* allow allocating addresses outside
            of this range if specifically requested.
        :type allocation_pools: A list of netaddr.IPRange.  None if not
            specified.
        """
        self._tenant_id = tenant_id
        self._subnet_id = subnet_id
        self._gateway_ip = None
        self._allocation_pools = None

        if gateway_ip is not None:
            self._gateway_ip = netaddr.IPAddress(gateway_ip)

        if allocation_pools is not None:
            allocation_pools = sorted(allocation_pools)
            previous = None
            for pool in allocation_pools:
                if not isinstance(pool, netaddr.ip.IPRange):
                    raise TypeError(_("Ranges must be netaddr.IPRange"))
                if previous and pool.first <= previous.last:
                    raise ValueError(_("Ranges must not overlap"))
                previous = pool
            if 1 < len(allocation_pools):
                # Checks that all the ranges are in the same IP version.
                # IPRange sorts first by ip version so we can get by with just
                # checking the first and the last range having sorted them
                # above.
                first_version = allocation_pools[0].version
                last_version = allocation_pools[-1].version
                if first_version != last_version:
                    raise ValueError(_("Ranges must be in the same IP "
                                       "version"))
            self._allocation_pools = allocation_pools

        if self.gateway_ip and self.allocation_pools:
            if self.gateway_ip.version != self.allocation_pools[0].version:
                raise ValueError(_("Gateway IP version inconsistent with "
                                   "allocation pool version"))

    @property
    def tenant_id(self):
        return self._tenant_id

    @property
    def subnet_id(self):
        return self._subnet_id

    @property
    def gateway_ip(self):
        return self._gateway_ip

    @property
    def allocation_pools(self):
        return self._allocation_pools

    def _validate_with_subnet(self, subnet_cidr):
        if self.allocation_pools:
            if subnet_cidr.version != self.allocation_pools[0].version:
                raise ipam_exc.IpamValueInvalid(_(
                                "allocation_pools use the wrong ip version"))
            for pool in self.allocation_pools:
                if pool not in subnet_cidr:
                    raise ipam_exc.IpamValueInvalid(_(
                                "allocation_pools are not in the subnet"))


class AnySubnetRequest(SubnetRequest):
    """A template for allocating an unspecified subnet from IPAM

    Support for this type of request in a driver is optional. For example, the
    initial reference implementation will not support this.  The API has no way
    of creating a subnet without a specific address until subnet-allocation is
    implemented.
    """
    WILDCARDS = {constants.IPv4: '0.0.0.0',
                 constants.IPv6: '::'}

    # DMH : Added name=None
    def __init__(self, tenant_id, subnet_id, version, prefixlen,
                 gateway_ip=None, allocation_pools=None, name=None):
        """
        :param version: Either constants.IPv4 or constants.IPv6
        :param prefixlen: The prefix len requested.  Must be within the min and
            max allowed.
        :type prefixlen: int
        """
        super(AnySubnetRequest, self).__init__(
            tenant_id=tenant_id,
            subnet_id=subnet_id,
            gateway_ip=gateway_ip,
            allocation_pools=allocation_pools)

        net = netaddr.IPNetwork(self.WILDCARDS[version] + '/' + str(prefixlen))
        self._validate_with_subnet(net)

        self._prefixlen = prefixlen
        
        # DMH
        self.name = name

    @property
    def prefixlen(self):
        return self._prefixlen


class SpecificSubnetRequest(SubnetRequest):
    """A template for allocating a specified subnet from IPAM

    The initial reference implementation will probably just allow any
    allocation, even overlapping ones.  This can be expanded on by future
    blueprints.
    """
    # DMH : Added name=None
    def __init__(self, tenant_id, subnet_id, subnet_cidr,
                 gateway_ip=None, allocation_pools=None, name=None):
        """
        :param subnet: The subnet requested.  Can be IPv4 or IPv6.  However,
            when IPAM tries to fulfill this request, the IP version must match
            the version of the address scope being used.
        :type subnet: netaddr.IPNetwork or convertible to one
        """
        super(SpecificSubnetRequest, self).__init__(
            tenant_id=tenant_id,
            subnet_id=subnet_id,
            gateway_ip=gateway_ip,
            allocation_pools=allocation_pools,
            )

        #DMH
        self.name = name
        
        self._subnet_cidr = netaddr.IPNetwork(subnet_cidr)
        self._validate_with_subnet(self._subnet_cidr)

    @property
    def subnet_cidr(self):
        return self._subnet_cidr

    @property
    def prefixlen(self):
        return self._subnet_cidr.prefixlen


@six.add_metaclass(abc.ABCMeta)
class AddressRequest(object):
    """Abstract base class for address requests"""


class SpecificAddressRequest(AddressRequest):
    """For requesting a specified address from IPAM"""
    def __init__(self, address, port_name=None, params=None):
        """
        :param address: The address being requested
        :type address: A netaddr.IPAddress or convertible to one.
        """
        super(SpecificAddressRequest, self).__init__()
        self._address = netaddr.IPAddress(address)
        
        LOG.info("BCN: SpecificAddressRequest params:%s" %(params))

        if params:
            if not params['port_name'] == "":         
                LOG.info("BCN: port_name = %s" % (params['port_name']))
                self.port_name = params['port_name']
            else:
                LOG.info("BCN: port_name not set")
                
            if not params['mac'] == "":         
                LOG.info("BCN: mac = %s" % (params['mac']))
                self.mac_address = params['mac']
            else:
                LOG.info("BCN: mac not set")
        else:
            LOG.info("BCN: params not set")
        
    @property
    def address(self):
        return self._address


class AnyAddressRequest(AddressRequest):
    """Used to request any available address from the pool."""

    def __init__(self, port_name=None, id=None, params=None):

            super(AnyAddressRequest, self).__init__()

            LOG.info("BCN: AnyAddressRequest params:%s" %(params))

            if params:
                if not params['port_name'] == "":
                    LOG.info("BCN: port_name = %s" % (params['port_name']))
                    self.port_name = params['port_name']
                else:
                    LOG.info("BCN: port_name not set")
     
                if not params['id'] == "":
                    LOG.info("BCN: id = %s" % (params['id']))
                    self.id = params['id']
                else:
                    LOG.info("BCN: id not set")
                    
                if not params['mac'] == "":         
                    LOG.info("BCN: mac = %s" % (params['mac']))
                    self.mac_address = params['mac']
                else:
                    LOG.info("BCN: mac not set")
            else:
                LOG.info("BCN: params not set\n")
                

class PreferNextAddressRequest(AnyAddressRequest):
    """Used to request next available IP address from the pool."""
    
    def __init__(self, port_name=None, id=None, params=None):

            super(PreferNextAddressRequest, self).__init__()

            LOG.info("BCN: PreferNextAddressRequest params:%s" %(params))
            
            if params:
                if not params['port_name'] == "":
                    LOG.info("BCN: port_name = %s" % (params['port_name']))
                    self.port_name = params['port_name']
                else:
                    LOG.info("BCN: port_name not set")
                     
                if not params['id'] == "":
                    LOG.info("BCN: id = %s" % (params['id']))
         
                    self.id = params['id']
                else:
                    LOG.info("BCN: id not set")
                    
                if not params['mac'] == "":         
                    LOG.info("BCN: mac = %s" % (params['mac']))
                    self.mac_address = params['mac']
                else:
                    LOG.info("BCN: mac not set")
            else:
                LOG.info("BCN: params not set\n")               
            


class AutomaticAddressRequest(SpecificAddressRequest):
    """Used to create auto generated addresses, such as EUI64"""
    EUI64 = 'eui64'

    def _generate_eui64_address(self, **kwargs):
        if set(kwargs) != set(['prefix', 'mac']):
            raise ipam_exc.AddressCalculationFailure(
                address_type='eui-64',
                reason=_('must provide exactly 2 arguments - cidr and MAC'))
        prefix = kwargs['prefix']
        mac_address = kwargs['mac']
        return netutils.get_ipv6_addr_by_EUI64(prefix, mac_address)

    _address_generators = {EUI64: _generate_eui64_address}

    def __init__(self, address_type=EUI64, **kwargs):
        """
        This constructor builds an automatic IP address. Parameter needed for
        generating it can be passed as optional keyword arguments.

        :param address_type: the type of address to generate.
            It could be an eui-64 address, a random IPv6 address, or
            an ipv4 link-local address.
            For the Kilo release only eui-64 addresses will be supported.
        """
        address_generator = self._address_generators.get(address_type)
        if not address_generator:
            raise ipam_exc.InvalidAddressType(address_type=address_type)
        address = address_generator(self, **kwargs)
        super(AutomaticAddressRequest, self).__init__(address)


class RouterGatewayAddressRequest(AddressRequest):
    """Used to request allocating the special router gateway address."""


class AddressRequestFactory(object):
    """Builds request using ip info

    Additional parameters(port and context) are not used in default
    implementation, but planned to be used in sub-classes
    provided by specific ipam driver,
    """

    @classmethod
    def get_request(cls, context, port, ip_dict):
        """
        :param context: context (not used here, but can be used in sub-classes)
        :param port: port dict (not used here, but can be used in sub-classes)
        :param ip_dict: dict that can contain 'ip_address', 'mac' and
            'subnet_cidr' keys. Request to generate is selected depending on
             this ip_dict keys.
        :return: returns prepared AddressRequest (specific or any)
        """
        
        LOG.info("BCN: get_request()")
        for i in port.keys():
            LOG.info("BCN: key: [%s] value: [%s]" % (i, port[i]))

        id = port.get('id')     
        
        mac = port.get('mac_address')

        params = {}
        params['id'] = id
        params['port_name'] = port.get('name')
        if params['port_name'] == "":
            params['port_name'] = params['id'] 
        params['mac'] = mac
        
        LOG.info("BCN: get_request params:%s" %(params))
       
        if ip_dict.get('ip_address'):
            LOG.info("BCN: calling SpecificAddressRequest")
            return SpecificAddressRequest(ip_dict['ip_address'], params)
        elif ip_dict.get('eui64_address'):
            LOG.info("BCN: calling AutomaticAddressRequest")
            return AutomaticAddressRequest(prefix=ip_dict['subnet_cidr'],
                                           mac=ip_dict['mac'])
        elif port['device_owner'] == constants.DEVICE_OWNER_DHCP:
            LOG.info("BCN: calling PreferNextAddressRequest")
            return PreferNextAddressRequest(params=params)
        else:
            LOG.info("BCN: AnyAddressRequest")
            return AnyAddressRequest(params=params)



class SubnetRequestFactory(object):
    """Builds request using subnet info"""

    @classmethod
    def get_request(cls, context, subnet, subnetpool):

        LOG.info("BCN: get_request subnet: %s" % subnet)
        cidr = subnet.get('cidr') 
        
        name = None 
        if 'name' in subnet.keys():
	    name = subnet.get('name')
            
        LOG.info("BCN: Request - set subnet.name : %s" % (name))

        subnet_id = subnet.get('id', uuidutils.generate_uuid())
        is_any_subnetpool_request = not validators.is_attr_set(cidr)

        if is_any_subnetpool_request:
            prefixlen = subnet['prefixlen']
            if not validators.is_attr_set(prefixlen):
                prefixlen = int(subnetpool['default_prefixlen'])

            LOG.info("BCN: returning AnySubnetRequest, name=%s" %(name))
            return AnySubnetRequest(
                subnet['tenant_id'],
                subnet_id,
                common_utils.ip_version_from_int(subnetpool['ip_version']),
                prefixlen,
                None,
                None,
                name)
        else:
            LOG.info("BCN: returning SpecificSubnetRequest")

            return SpecificSubnetRequest(subnet['tenant_id'],
                                         subnet_id,
                                         cidr,
                                         subnet.get('gateway_ip'),
                                         subnet.get('allocation_pools'),
					 name)


