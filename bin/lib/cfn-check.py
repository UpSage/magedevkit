#!/usr/bin/env python3
"""Checks the pipeline kit's CloudFormation template (deploy/infra/stack.json)
without AWS — the pipeline wizard runs it on every kit it writes.

  cfn-check.py TEMPLATE [PARAMS]   → one line per problem, exit 1; nothing, exit 0

  * the template is JSON with only CloudFormation's top-level sections;
  * every Ref, Fn::GetAtt, Fn::Sub ${X} / ${X.Attr}, DependsOn and Condition
    names a Parameter, Resource, Condition or AWS:: pseudo parameter that
    exists;
  * every resource Type is one the kit is meant to use (catches typos);
  * no parameter named like a secret lacks NoEcho — the kit takes no secrets
    as parameters (they go to SSM, written by the provision wizard);
  * PARAMS (deploy/infra/<env>.params.json, the list create-change-set takes)
    sets exactly the template's parameters that have no Default, and no
    unknown ones;
  * compact, it fits the 51,200 bytes CloudFormation accepts inline.

Standard library only.
"""

import json
import re
import sys

SECTIONS = {"AWSTemplateFormatVersion", "Description", "Metadata", "Parameters", "Rules", "Mappings",
            "Conditions", "Transform", "Resources", "Outputs"}
PSEUDO = {"AWS::AccountId", "AWS::NotificationARNs", "AWS::NoValue", "AWS::Partition", "AWS::Region",
          "AWS::StackId", "AWS::StackName", "AWS::URLSuffix"}
TYPES = {
    "AWS::EC2::VPC", "AWS::EC2::InternetGateway", "AWS::EC2::VPCGatewayAttachment", "AWS::EC2::Subnet",
    "AWS::EC2::RouteTable", "AWS::EC2::Route", "AWS::EC2::SubnetRouteTableAssociation", "AWS::EC2::SecurityGroup",
    "AWS::EC2::Instance", "AWS::EC2::LaunchTemplate", "AWS::EC2::EIP", "AWS::EC2::EIPAssociation",
    "AWS::IAM::Role", "AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::InstanceProfile",
    "AWS::ECR::Repository", "AWS::S3::Bucket", "AWS::S3::BucketPolicy", "AWS::Logs::LogGroup",
    "AWS::CodeBuild::Project", "AWS::CodeDeploy::Application", "AWS::CodeDeploy::DeploymentGroup",
    "AWS::CodePipeline::Pipeline", "AWS::Route53::RecordSet", "AWS::Scheduler::Schedule",
    "AWS::SSM::Parameter", "AWS::SSM::Document",
}
SECRET_NAME = re.compile(r"(?i)password|secret|token|crypt|auth")
SUB_VAR = re.compile(r"\$\{([^}!][^}]*)\}")
INLINE_LIMIT = 51200


def walk(node, path, found):
    """Collects (path, kind, target) for every reference inside NODE."""
    if isinstance(node, dict):
        for k, v in node.items():
            here = "%s.%s" % (path, k)
            if k == "Ref" and isinstance(v, str):
                found.append((here, "Ref", v))
            elif k == "Fn::GetAtt":
                target = v[0] if isinstance(v, list) and v else v.split(".", 1)[0] if isinstance(v, str) else None
                found.append((here, "GetAtt", target))
            elif k == "Fn::Sub":
                text, own = (v[0], set(v[1]) if len(v) > 1 and isinstance(v[1], dict) else set()) \
                    if isinstance(v, list) else (v, set())
                if isinstance(text, str):
                    for name in SUB_VAR.findall(text):
                        base = name.split(".", 1)[0] if not name.startswith("AWS::") else name
                        if base not in own:
                            found.append((here, "Sub", base))
                if isinstance(v, list) and len(v) > 1:
                    walk(v[1], here, found)
                continue
            elif k == "Condition" and isinstance(v, str):  # inside Fn::And / Fn::Or / Fn::Not
                found.append((here, "Condition", v))
            elif k == "Fn::If" and isinstance(v, list) and v and isinstance(v[0], str):
                found.append((here, "Condition", v[0]))
            walk(v, here, found)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, "%s[%d]" % (path, i), found)


def check(template, params=None):
    problems = []
    if not isinstance(template, dict):
        return ["the template isn't a JSON object"]
    for k in template:
        if k not in SECTIONS:
            problems.append("unknown top-level section %s" % k)
    parameters = template.get("Parameters", {}) or {}
    resources = template.get("Resources", {}) or {}
    conditions = template.get("Conditions", {}) or {}
    if not resources:
        problems.append("no Resources")
    names = set(parameters) | set(resources) | PSEUDO

    for name, spec in parameters.items():
        if SECRET_NAME.search(name) and not spec.get("NoEcho"):
            problems.append("parameter %s looks like a secret but has no NoEcho (secrets belong in SSM)" % name)

    for name, res in resources.items():
        if not isinstance(res, dict):
            problems.append("resource %s isn't an object" % name)
            continue
        if res.get("Type") not in TYPES:
            problems.append("resource %s has an unexpected Type %s" % (name, res.get("Type")))
        deps = res.get("DependsOn", [])
        for d in [deps] if isinstance(deps, str) else deps:
            if d not in resources:
                problems.append("resource %s DependsOn %s, which doesn't exist" % (name, d))
        cond = res.get("Condition")
        if cond is not None and cond not in conditions:
            problems.append("resource %s uses Condition %s, which doesn't exist" % (name, cond))

    # References live in resource properties, output values and conditions
    # (a resource's or output's own Condition key is checked above/below).
    found = []
    for name, res in resources.items():
        if isinstance(res, dict):
            walk(res.get("Properties", {}), "Resources.%s.Properties" % name, found)
    for name, out in (template.get("Outputs", {}) or {}).items():
        if isinstance(out, dict):
            walk(out.get("Value"), "Outputs.%s.Value" % name, found)
    walk(conditions, "Conditions", found)
    for where, kind, target in found:
        if kind == "Condition":
            if target not in conditions:
                problems.append("%s: Condition %s doesn't exist" % (where, target))
        elif kind == "GetAtt":
            if target not in resources:
                problems.append("%s: Fn::GetAtt names %s, which isn't a resource" % (where, target))
        elif target not in names:
            problems.append("%s: %s %s doesn't exist" % (where, kind, target))

    for name, out in (template.get("Outputs", {}) or {}).items():
        cond = out.get("Condition") if isinstance(out, dict) else None
        if cond is not None and cond not in conditions:
            problems.append("output %s uses Condition %s, which doesn't exist" % (name, cond))

    size = len(json.dumps(template, separators=(",", ":")))
    if size > INLINE_LIMIT:
        problems.append("the compact template is %d bytes — over CloudFormation's %d-byte inline limit" % (size, INLINE_LIMIT))

    if params is not None:
        if not isinstance(params, list) or not all(isinstance(p, dict) and "ParameterKey" in p for p in params):
            problems.append("the parameters file isn't a list of {ParameterKey, ParameterValue}")
        else:
            given = [p["ParameterKey"] for p in params]
            for k in sorted(set(given) - set(parameters)):
                problems.append("the parameters file sets %s, which the template doesn't have" % k)
            for k in sorted(k for k, v in parameters.items() if "Default" not in v and k not in given):
                problems.append("the parameters file doesn't set %s (it has no default)" % k)
            for k in sorted(k for k in set(given) if given.count(k) > 1):
                problems.append("the parameters file sets %s more than once" % k)
            for p in params:
                spec = parameters.get(p["ParameterKey"], {})
                allowed = spec.get("AllowedValues")
                value = str(p.get("ParameterValue", ""))
                if allowed and value not in [str(a) for a in allowed]:
                    problems.append("%s=%r isn't one of %s" % (p["ParameterKey"], value, allowed))
                pattern = spec.get("AllowedPattern")
                if pattern and not re.fullmatch(pattern, value):
                    problems.append("%s=%r doesn't match %s" % (p["ParameterKey"], value, pattern))
    return problems


def main(argv):
    if not 1 <= len(argv) <= 2:
        sys.stderr.write("Usage: cfn-check.py TEMPLATE [PARAMS]\n")
        return 2
    loaded = []
    for path in argv:
        try:
            with open(path, encoding="utf-8") as f:
                loaded.append(json.load(f))
        except (OSError, ValueError) as e:
            print("%s: not readable JSON (%s)" % (path, e))
            return 1
    problems = check(loaded[0], loaded[1] if len(loaded) > 1 else None)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
