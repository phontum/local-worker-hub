"""Typed experiment limits; permissions still come from explicit task scopes/checks."""
from pydantic import BaseModel, ConfigDict, Field


class ExperimentPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool=False
    shadow: bool=False
    sample_rate: float=Field(default=.2,ge=0,le=1)
    daily_shadows: int=Field(default=10,ge=0,le=100)
    minimize: bool=False
    mutation: bool=False
    slicing: bool=False
    graph: bool=False
    recipes: bool=False
    seconds: int=Field(default=60,ge=1,le=300)
    trials: int=Field(default=12,ge=1,le=50)
    mutants: int=Field(default=10,ge=1,le=50)
    cheap_check_names: list[str]=Field(default_factory=list,max_length=12)
    required_causal_test: str | None=None
    fix_paths: list[str]=Field(default_factory=list,max_length=30)
    stub_files: dict[str,str]=Field(default_factory=dict,max_length=20)
    metamorphic_cases: list['MetamorphicCase']=Field(default_factory=list,max_length=10)


class MetamorphicEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path: str = Field(min_length=1,max_length=300)
    old: str = Field(min_length=1,max_length=4000)
    new: str = Field(max_length=4000)
    expected_count: int = Field(default=1,ge=1,le=1000)


class MetamorphicCase(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=120)
    edits: list[MetamorphicEdit] = Field(min_length=1,max_length=20)
    check_names: list[str] = Field(min_length=1,max_length=12)
    expected: str = Field(pattern=r'^(passed|assertion_failed)$')
    test_id: str | None = Field(default=None,max_length=300)
    required: bool = False

ExperimentPolicy.model_rebuild()
