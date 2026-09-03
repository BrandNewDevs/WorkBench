import { Container, Flex, Heading, Text, VStack } from "@chakra-ui/react";

export function App() {
  return (
    <Flex minH="100vh" align="center" justify="center" px="6">
      <Container maxW="container.md">
        <VStack gap="4" textAlign="center">
          <Heading size="4xl" letterSpacing="-0.03em">
            WorkBench
          </Heading>
          <Text fontSize="lg" color="gray.600">
            The Agentic tool that doesn't leak your data.
          </Text>
        </VStack>
      </Container>
    </Flex>
  );
}
